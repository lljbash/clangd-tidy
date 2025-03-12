import asyncio
import os
import pathlib
import sys
from typing import Union

from .client import ClientAsync, RequestResponsePair
from .messages import (
    DidOpenTextDocumentParams,
    DocumentFormattingParams,
    InitializeParams,
    LanguageId,
    LspNotificationMessage,
    NotificationMethod,
    RequestMethod,
    TextDocumentIdentifier,
    TextDocumentItem,
    WorkspaceFolder,
)
from .rpc import RpcEndpointAsync

__all__ = ["ClangdAsync"]


class ClangdAsync:
    def __init__(
        self,
        clangd_executable: str,
        *,
        compile_commands_dir: str,
        jobs: int,
        verbose: bool,
        query_driver: str,
    ):
        self._clangd_cmd = [
            clangd_executable,
            f"--compile-commands-dir={compile_commands_dir}",
            "--clang-tidy",
            f"--j={jobs}",
            "--pch-storage=memory",
            "--enable-config",
        ]
        if query_driver:
            self._clangd_cmd.append(f"--query-driver={query_driver}")
        self._stderr = sys.stderr if verbose else open(os.devnull, "w")

    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *self._clangd_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self._stderr,
        )
        assert self._process.stdin is not None and self._process.stdout is not None
        rpc = RpcEndpointAsync(self._process.stdout, self._process.stdin)
        self._client = ClientAsync(rpc)

    async def recv_response_or_notification(
        self,
    ) -> Union[RequestResponsePair, LspNotificationMessage]:
        return await self._client.recv()

    async def initialize(self, root: pathlib.Path) -> None:
        assert root.is_dir()
        await self._client.request(
            RequestMethod.INITIALIZE,
            InitializeParams(
                processId=self._process.pid,
                workspaceFolders=[
                    WorkspaceFolder(name="foo", uri=root.as_uri()),
                ],
            ),
        )

    async def initialized(self) -> None:
        await self._client.notify(NotificationMethod.INITIALIZED)

    async def did_open(self, path: pathlib.Path) -> None:
        assert path.is_file()
        await self._client.notify(
            NotificationMethod.DID_OPEN,
            DidOpenTextDocumentParams(
                TextDocumentItem(
                    uri=path.as_uri(),
                    languageId=LanguageId.CPP,
                    version=1,
                    text=path.read_text(),
                )
            ),
        )

    async def formatting(self, path: pathlib.Path) -> None:
        assert path.is_file()
        await self._client.request(
            RequestMethod.FORMATTING,
            DocumentFormattingParams(
                textDocument=TextDocumentIdentifier(uri=path.as_uri()), options={}
            ),
        )

    async def shutdown(self) -> None:
        await self._client.request(RequestMethod.SHUTDOWN)

    async def exit(self) -> None:
        await self._client.notify(NotificationMethod.EXIT)
        self._process.kill()  # PERF: much faster than waiting for clangd to exit
        await self._process.wait()

        # HACK: prevent RuntimeError('Event loop is closed') before Python 3.11
        # see https://github.com/python/cpython/issues/88050
        if sys.version_info < (3, 11):
            self._process._transport.close()  # type: ignore

        self._stderr.close()
