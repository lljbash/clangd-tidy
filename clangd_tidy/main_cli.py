#!/usr/bin/env python3

import asyncio
import pathlib
import sys
from typing import Collection, List, TextIO
from urllib.parse import unquote, urlparse

import cattrs
from tqdm import tqdm

from .args import parse_args, SEVERITY_INT
from .diagnostic_formatter import (
    CompactDiagnosticFormatter,
    DiagnosticCollection,
    FancyDiagnosticFormatter,
    GithubActionWorkflowCommandDiagnosticFormatter,
)
from .lsp import ClangdAsync, RequestResponsePair
from .lsp.messages import (
    Diagnostic,
    DocumentFormattingParams,
    LspNotificationMessage,
    NotificationMethod,
    Position,
    PublishDiagnosticsParams,
    Range,
    RequestMethod,
)

__all__ = ["main_cli"]


def _uri_to_path(uri: str) -> pathlib.Path:
    return pathlib.Path(unquote(urlparse(uri).path))


def _is_output_supports_color(output: TextIO) -> bool:
    return hasattr(output, "isatty") and output.isatty()


class ClangdRunner:
    def __init__(
        self,
        clangd: ClangdAsync,
        files: Collection[pathlib.Path],
        run_format: bool,
        tqdm: bool,
        max_pending_requests: int,
    ):
        self._clangd = clangd
        self._files = files
        self._run_format = run_format
        self._tqdm = tqdm
        self._max_pending_requests = max_pending_requests

    def acquire_diagnostics(self) -> DiagnosticCollection:
        return asyncio.run(self._acquire_diagnostics())

    async def _request_diagnostics(self) -> None:
        self._sem = asyncio.Semaphore(self._max_pending_requests)
        for file in self._files:
            await self._sem.acquire()
            await self._clangd.did_open(file)
            if self._run_format:
                await self._sem.acquire()
                await self._clangd.formatting(file)

    async def _collect_diagnostics(self) -> DiagnosticCollection:
        diagnostics: DiagnosticCollection = {}
        formatting_diagnostics: DiagnosticCollection = (
            {} if self._run_format else {file: [] for file in self._files}
        )
        nfiles = len(self._files)
        with tqdm(
            total=nfiles,
            desc="Collecting diagnostics",
            disable=not self._tqdm,
        ) as pbar:
            while len(diagnostics) < nfiles or len(formatting_diagnostics) < nfiles:
                resp = await self._clangd.recv_response_or_notification()
                if isinstance(resp, LspNotificationMessage):
                    if resp.method == NotificationMethod.PUBLISH_DIAGNOSTICS:
                        params = cattrs.structure(resp.params, PublishDiagnosticsParams)
                        file = _uri_to_path(params.uri)
                        diagnostics[file] = params.diagnostics
                        tqdm.update(pbar)
                        self._sem.release()
                else:
                    assert resp.request.method == RequestMethod.FORMATTING
                    assert resp.response.error is None, "Formatting failed"
                    params = cattrs.structure(
                        resp.request.params, DocumentFormattingParams
                    )
                    file = _uri_to_path(params.textDocument.uri)
                    formatting_diagnostics[file] = (
                        [
                            Diagnostic(
                                range=Range(start=Position(0, 0), end=Position(0, 0)),
                                message="File does not conform to the formatting rules (run `clang-format` to fix)",
                                source="clang-format",
                            )
                        ]
                        if resp.response.result
                        else []
                    )
                    self._sem.release()
        return {
            file: formatting_diagnostics[file] + diagnostics[file]
            for file in self._files
        }

    async def _acquire_diagnostics(self) -> DiagnosticCollection:
        await self._clangd.start()
        await self._clangd.initialize(pathlib.Path.cwd())
        init_resp = await self._clangd.recv_response_or_notification()
        assert isinstance(init_resp, RequestResponsePair)
        assert init_resp.request.method == RequestMethod.INITIALIZE
        assert init_resp.response.error is None, "Initialization failed"
        await self._clangd.initialized()
        _, file_diagnostics = await asyncio.gather(
            self._request_diagnostics(), self._collect_diagnostics()
        )
        await self._clangd.shutdown()
        await self._clangd.exit()
        return file_diagnostics


def main_cli():
    args = parse_args()

    files: List[pathlib.Path] = args.filename
    files = [
        file.resolve() for file in files if file.suffix[1:] in args.allow_extensions
    ]
    missing_files = [str(file) for file in files if not file.is_file()]
    if missing_files:
        print(f"File(s) not found: {', '.join(missing_files)}", file=sys.stderr)
        sys.exit(1)

    file_diagnostics = ClangdRunner(
        clangd=ClangdAsync(
            args.clangd_executable, args.compile_commands_dir, args.jobs, args.verbose
        ),
        files=files,
        run_format=args.format,
        tqdm=args.tqdm,
        max_pending_requests=args.jobs * 2,
    ).acquire_diagnostics()

    formatter = (
        FancyDiagnosticFormatter(
            extra_context=args.context,
            enable_color=(
                _is_output_supports_color(args.output)
                if args.color == "auto"
                else args.color == "always"
            ),
        )
        if not args.compact
        else CompactDiagnosticFormatter()
    )
    print(formatter.format(file_diagnostics), file=args.output)
    if args.github:
        print(
            GithubActionWorkflowCommandDiagnosticFormatter(args.git_root).format(
                file_diagnostics
            ),
            file=args.output,
        )
    if any(
        any(
            (
                diagostic.severity
                and diagostic.severity >= SEVERITY_INT[args.fail_on_severity]
            )
            or diagostic.source == "clang-format"
            for diagostic in diagnostics
        )
        for diagnostics in file_diagnostics.values()
    ):
        exit(1)
