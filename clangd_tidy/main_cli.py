#!/usr/bin/env python3

import asyncio
import importlib.util
import pathlib
import sys
from typing import Collection, List, TextIO
from urllib.parse import unquote, urlparse

import cattrs
from tqdm import tqdm

from .args import parse_args, SEVERITY_INT
from .diagnostic_formatter import (
    CompactDiagnosticFormatter,
    FancyDiagnosticFormatter,
    FileDiagnostics,
    GithubActionWorkflowCommandDiagnosticFormatter,
)
from .lsp import ClangdAsync, RequestResponsePair
from .lsp.messages import (
    LspNotificationMessage,
    NotificationMethod,
    PublishDiagnosticsParams,
)

__all__ = ["main_cli"]


def _uri_to_path(uri: str) -> pathlib.Path:
    return pathlib.Path(unquote(urlparse(uri).path))


def _is_output_supports_color(output: TextIO) -> bool:
    return hasattr(output, "isatty") and output.isatty()


class ClangdRunner:
    def __init__(
        self, clangd: ClangdAsync, files: Collection[pathlib.Path], tqdm: bool
    ):
        self._clangd = clangd
        self._files = files
        self._tqdm = tqdm

    def run(self) -> List[FileDiagnostics]:
        return asyncio.run(self._acquire_diagnostics())

    async def _request_diagnostics(self) -> None:
        await asyncio.gather(*(self._clangd.did_open(file) for file in self._files))

    async def _collect_diagnostics(self) -> List[FileDiagnostics]:
        file_diagnostics: List[FileDiagnostics] = []
        with tqdm(
            total=len(self._files),
            desc="Collecting diagnostics",
            disable=not self._tqdm,
        ) as pbar:
            while len(file_diagnostics) < len(self._files):
                resp = await self._clangd.recv_response_or_notification()
                if isinstance(resp, LspNotificationMessage):
                    if resp.method == NotificationMethod.PUBLISH_DIAGNOSTICS:
                        params = cattrs.structure(resp.params, PublishDiagnosticsParams)
                        file = _uri_to_path(params.uri)
                        file_diagnostics.append(
                            FileDiagnostics(file, params.diagnostics)
                        )
                        tqdm.update(pbar)
        return file_diagnostics

    async def _acquire_diagnostics(self) -> List[FileDiagnostics]:
        await self._clangd.start()
        await self._clangd.initialize(pathlib.Path.cwd())
        init_resp = await self._clangd.recv_response_or_notification()
        assert isinstance(init_resp, RequestResponsePair)
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
        ClangdAsync(
            args.clangd_executable, args.compile_commands_dir, args.jobs, args.verbose
        ),
        files,
        args.tqdm,
    ).run()

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
            d.severity and d.severity >= SEVERITY_INT[args.fail_on_severity]
            for d in fd.diagnostics
        )
        for fd in file_diagnostics
    ):
        exit(1)
