import asyncio
import logging
import pathlib
from typing import Collection, List

from .base import BaseFlow

from ..event_bus import EventBus
from ..lsp import ClangdAsync
from ..lsp.messages import (
    Diagnostic,
    PublishDiagnosticsNotificationMessage,
    uri_to_path,
)


class DiagnosticsFlow(BaseFlow):
    """
    Manages diagnostics collection for multiple files via clangd.

    This flow orchestrates diagnostic gathering by:
    - Opening files with textDocument/didOpen notifications
    - Collecting textDocument/publishDiagnostics notifications from clangd
    - Tracking completion state per file using futures

    Flow diagram:
    ┌────────────────────────────────────────┬──────────────────────────────────┐
    │ Main Flow (run())                      │ Event Handler (async)            │
    ├────────────────────────────────────────┼──────────────────────────────────┤
    │ 1. spawn_task per file                 │                                  │
    │ 2. create_future((file, 'diagnostics'))│                                  │
    │ 3. send didOpen → clangd               │                                  │
    │ 4. await diagnostics future (blocked)  │ ← clangd sends publishDiagnostics│
    │                                        │ → _publish_diagnostics_cb()      │
    │                                        │ → resolve_future() (unblocks)    │
    │ 5. return diagnostics                  │                                  │
    └────────────────────────────────────────┴──────────────────────────────────┘

    Note: When clangd restarts after a crash, files are reopened and new
    diagnostics are received. The flow handles these duplicates gracefully.
    """

    def __init__(
        self,
        clangd_client: ClangdAsync,
        event_bus: EventBus,
        files: Collection[pathlib.Path],
    ):
        super().__init__(clangd_client, event_bus)
        self._files = set(files)

        self._event_bus.subscribe(
            PublishDiagnosticsNotificationMessage, self._publish_diagnostics_cb
        )

    async def run(self) -> None:
        logging.debug(f"Spawning diagnostics flows for {len(self._files)} files")
        for path in self._files:
            if path in self._futures and self._futures[path].done():
                # Skip already completed diagnostics flows
                continue
            self._spawn_task(path, self._open_and_get_diagnostics(path))

    async def _open_and_get_diagnostics(self, path: pathlib.Path) -> List[Diagnostic]:
        """
        Get the diagnostics for a single file.

        1. --> textDocument/didOpen (Notify `clangd` that the file is open.)
        2. --- Wait for `clangd` to publish diagnostics for the file.
        """
        try:
            # Create or re-use the diagnostics future for this file. We always want to
            # open the file, but we might be able to re-use an existing diagnostics.
            if (path, "diagnostics") not in self._futures:
                diagnostics_future: asyncio.Future[List[Diagnostic]] = (
                    self._create_future((path, "diagnostics"))
                )
            else:
                diagnostics_future = self._futures[(path, "diagnostics")]
            await self._clangd.ensure_file_opened(path)
            diagnostics = await diagnostics_future
            return diagnostics
        except (ConnectionError, EOFError) as e:
            logging.warning(
                f"Diagnostics failed for '{path}' due to connection error and will be retried: {e}"
            )
            raise
        except Exception as e:
            logging.warning(f"Diagnostics failed for '{path}': {e}", exc_info=True)
            raise

    async def _publish_diagnostics_cb(
        self,
        message: PublishDiagnosticsNotificationMessage,
    ):
        """
        Processes a PublishDiagnostics notification from `clangd`.

        This simply resolves the corresponding Future with the received diagnostics,
        unblocking the `_get_file_diagnostics` method.
        """
        path = uri_to_path(message.params.uri)
        logging.debug(
            f"Received {len(message.params.diagnostics)} diagnostics for {path}"
        )
        # Set the internal future to unblock _get_file_diagnostics.
        # We also receive diagnostics for files we don't track (such as .clangd),
        # so we skip them.
        if path in self._files:
            self._resolve_future((path, "diagnostics"), message.params.diagnostics)

    def get_diagnostics(self) -> dict[pathlib.Path, List[Diagnostic]]:
        """Get all collected diagnostics from completed file flows."""
        results: dict[pathlib.Path, List[Diagnostic]] = {}
        for key, future in self._futures.items():
            # From all the futures, extract only the diagnostics futures and return their diagnostics
            match key:
                case (pathlib.Path() as path, "diagnostics"):
                    if not future.done():
                        raise RuntimeError(
                            f"Diagnostics for '{path}' are not yet complete"
                        )
                    results[path] = future.result()
                case _:
                    continue
        return results
