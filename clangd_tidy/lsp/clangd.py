import asyncio
import os
import pathlib
import sys
import logging
from typing import Callable, Optional, Set, Any, List

import cattrs

from ..event_bus import EventBus
from .client import ClientAsync
from .messages import (
    ClientCapabilities,
    CodeAction,
    CodeActionContext,
    CodeActionParams,
    Diagnostic,
    DidOpenTextDocumentParams,
    DocumentFormattingParams,
    ExecuteCommandParams,
    InitializeParams,
    LanguageId,
    NotificationMethod,
    Position,
    Range,
    RequestMethod,
    ResponseMessage,
    TextDocumentIdentifier,
    TextDocumentItem,
    WorkspaceFolder,
)
from .rpc import RpcEndpointAsync

__all__ = ["ClangdAsync"]

converter = cattrs.Converter()


class ClangdAsync:
    """
    An LSP client for clangd that manages the complexities of the Language
    Server Protocol, including asynchronous message handling, state tracking,
    and crash recovery.

    This class is responsible for:
    - Starting and stopping the clangd process.
    - Initializing the LSP session and advertising client capabilities.
    - Sending requests and notifications to the server.
    - Receiving and dispatching responses and notifications from the server.
    - Tracking the state of in-flight requests (diagnostics, code actions, etc.).
    - Handling background indexing progress notifications.
    - Orchestrating complex, multi-step interactions like applying a fix.
    - Recovering from clangd crashes by restarting the server and retrying
      incomplete operations.
    """

    def __init__(
        self,
        clangd_executable: str,
        event_bus: EventBus,
        *,
        compile_commands_dir: str,
        jobs: int,
        verbose: bool,
        query_driver: str,
        use_background_index: bool,
    ):
        self._clangd_cmd = [
            clangd_executable,
            f"--compile-commands-dir={compile_commands_dir}",
            "--clang-tidy",
            f"--j={jobs}",
            "--pch-storage=memory",
            "--enable-config",
        ]
        if use_background_index:
            self._clangd_cmd.append("--background-index")
        if query_driver:
            self._clangd_cmd.append(f"--query-driver={query_driver}")
        if verbose:
            self._clangd_cmd.append("-log=verbose")
        self._stderr = sys.stderr if verbose else open(os.devnull, "w")
        self._event_bus = event_bus

        max_pending_requests = jobs * 2
        self._semaphore = asyncio.Semaphore(max_pending_requests)

        # --- Runtime State ---
        # These attributes are reset every time clangd is (re)started.

        # The clangd subprocess.
        self._process: Optional[asyncio.subprocess.Process] = None
        # The low-level LSP client for sending/receiving messages.
        self._client: ClientAsync
        # The set of files that have been opened in the LSP session.
        self._opened_files: Set[pathlib.Path] = set()

    async def start(self) -> None:
        self._opened_files.clear()
        self._process = await asyncio.create_subprocess_exec(
            *self._clangd_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self._stderr,
        )
        assert self._process.stdin is not None and self._process.stdout is not None
        rpc = RpcEndpointAsync(self._process.stdout, self._process.stdin)
        self._client = ClientAsync(rpc)

    async def recv_and_publish(self) -> None:
        """Wait for a message from the server and publish it to the event bus."""
        if not self._client:
            raise RuntimeError("LSP client is not initialized. Call start() first.")
        resp = await self._client.recv()
        self._event_bus.publish(resp)

    async def initialize(
        self, root: pathlib.Path, capabilities: ClientCapabilities
    ) -> ResponseMessage:
        assert root.is_dir()

        if not self._client:
            raise RuntimeError("LSP client is not initialized. Call start() first.")
        if not self._process:
            raise RuntimeError("LSP process is not initialized. Call start() first.")
        return await self._client.request_and_await_response(
            RequestMethod.INITIALIZE,
            InitializeParams(
                processId=self._process.pid,
                workspaceFolders=[
                    WorkspaceFolder(name=root.name, uri=root.as_uri()),
                ],
                capabilities=capabilities,
            ),
        )

    async def initialized(self) -> None:
        if not self._client:
            raise RuntimeError("LSP client is not initialized. Call start() first.")
        await self._client.notify(NotificationMethod.INITIALIZED)

    async def _did_open(self, path: pathlib.Path) -> None:
        assert path.is_file()
        if not self._client:
            raise RuntimeError("LSP client is not initialized. Call start() first.")
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
        self._opened_files.add(path)

    async def ensure_file_opened(self, file: pathlib.Path) -> None:
        """Ensure a file is opened. Opens it if not already opened."""
        if file not in self._opened_files:
            await self._did_open(file)

    async def respond_to_server(self, request_id: int, result: Any) -> None:
        """Respond to a server request."""
        if not self._client:
            raise RuntimeError("LSP client is not initialized. Call start() first.")
        await self._client.respond(request_id, result)

    def select_best_fix_action(
        self, code_actions: list[CodeAction], diag_message: str
    ) -> CodeAction:
        """Select fix action for a diagnostic, filtering out unrelated tweaks.

        When requesting code actions for a diagnostic, clangd uses two mechanisms:
        1. It provides fix actions related to the diagnostic in the context.diagnostics
           field of the CodeActionParams.
        2. It also provides generic tweaks based on the range/cursor position.
        We only want the first kind, so we filter out the second. We also ensure exactly
        one fix action remains after filtering, as the alternative means we haven't
        identified the correct fix for the diagnostic.

        Raises RuntimeError if 0 or >1 fix actions remain after filtering.
        """
        logging.debug(f"Got {len(code_actions)} code actions for: {diag_message}")

        # Filter out clangd.applyTweak actions (generic tweaks unrelated to diagnostics)
        fix_actions = [
            action
            for action in code_actions
            if not (action.command and action.command.command == "clangd.applyTweak")
        ]

        if len(fix_actions) == 0:
            raise RuntimeError(
                f"Expected exactly 1 fix action, got 0 for: {diag_message}"
            )

        if len(fix_actions) > 1:
            titles = [a.title for a in fix_actions]
            raise RuntimeError(
                f"Expected exactly 1 fix action, got {len(fix_actions)} for: {diag_message}\n"
                f"Actions: {titles}"
            )

        logging.debug(f"Selected: {fix_actions[0].title}")
        return fix_actions[0]

    async def formatting(self, file: pathlib.Path) -> ResponseMessage:
        if not self._client:
            raise RuntimeError("LSP client is not initialized. Call start() first.")
        return await self._client.request_and_await_response(
            RequestMethod.FORMATTING,
            DocumentFormattingParams(
                textDocument=TextDocumentIdentifier(uri=file.as_uri())
            ),
        )

    async def code_action(
        self,
        file: pathlib.Path,
        range_start: Position,
        range_end: Position,
        diagnostics: list[Diagnostic],
    ) -> ResponseMessage:
        """Request code actions for a specific range with diagnostics.

        Awaits the response and returns the response message.
        """
        params = CodeActionParams(
            textDocument=TextDocumentIdentifier(uri=file.as_uri()),
            range=Range(start=range_start, end=range_end),
            context=CodeActionContext(diagnostics=diagnostics),
        )

        if not self._client:
            raise RuntimeError("LSP client is not initialized. Call start() first.")
        response = await self._client.request_and_await_response(
            RequestMethod.CODE_ACTION, params
        )
        if response.result is not None:
            response.result = cattrs.structure(response.result, List[CodeAction])
        return response

    async def execute_command(
        self,
        command: str,
        arguments: List[Any],
        request_id_cb: Optional[Callable[[int], Any]] = None,
    ) -> ResponseMessage:
        """
        Execute a workspace command and wait for the response.

        For complex tasks that require the request ID before getting the response,
        a callback can be provided to receive the request ID when the request is sent.

        Returns the response message from the server.
        """
        params = ExecuteCommandParams(
            command=command,
            arguments=arguments,
        )

        if not self._client:
            raise RuntimeError("LSP client is not initialized. Call start() first.")
        return await self._client.request_and_await_response(
            RequestMethod.EXECUTE_COMMAND, params, request_id_cb=request_id_cb
        )

    async def shutdown(self) -> None:

        if not self._client:
            raise RuntimeError("LSP client is not initialized. Call start() first.")
        await self._client.request(RequestMethod.SHUTDOWN)

    async def exit(self) -> None:
        if not self._client:
            raise RuntimeError("LSP client is not initialized. Call start() first.")
        if not self._process:
            raise RuntimeError("LSP process is not initialized. Call start() first.")
        await self._client.notify(NotificationMethod.EXIT)
        self._process.kill()  # Fastest way to ensure process is terminated
        await self._process.wait()

        # HACK: prevent RuntimeError('Event loop is closed') before Python 3.11
        # see https://github.com/python/cpython/issues/88050
        if sys.version_info < (3, 11):
            self._process._transport.close()  # type: ignore

        await self._client.shutdown()

        if self._stderr is not sys.stderr:
            self._stderr.close()

    def get_semaphore(self) -> asyncio.Semaphore:
        return self._semaphore
