import asyncio
import logging
from typing import Optional

import cattrs

from .flows.base import BaseFlow

from .lsp.clangd import ClangdAsync
from .event_bus import EventBus
from .lsp.messages import (
    ProgressNotificationMessage,
    WorkDoneProgressCreateRequest,
    WorkDoneProgressEnd,
)

# Cattrs converter
converter = cattrs.Converter()


class IndexingProgressTracker(BaseFlow):
    """
    Tracks the state of clangd's background indexing and provides a method to wait for it to complete.
    """

    def __init__(
        self,
        clangd_client: ClangdAsync,
        event_bus: EventBus,
    ):
        super().__init__(clangd_client, event_bus)

        self._indexing_token: Optional[str] = None
        self._is_indexing = False
        self._indexing_finished_event = asyncio.Event()
        self._event_bus.subscribe(
            WorkDoneProgressCreateRequest, self._handle_progress_create
        )
        self._event_bus.subscribe(
            ProgressNotificationMessage, self._handle_progress_notification
        )

    async def wait_for_indexing_to_finish(self):
        """Waits until the current indexing process has finished."""
        if self._is_indexing:
            await self._indexing_finished_event.wait()

    async def run(self):
        """Runs the tracker (no-op, exists to satisfy BaseFlow)."""
        pass

    async def bootstrap(self):
        """Resets the state of the tracker after crash."""
        logging.debug("Resetting IndexingProgressTracker state.")
        self._indexing_token = None
        self._is_indexing = False
        # Clear the event so wait_for_indexing_to_finish will wait for the next indexing cycle
        self._indexing_finished_event.clear()

    async def _handle_progress_create(self, request: WorkDoneProgressCreateRequest):
        """
        Handles the window/workDoneProgress/create request from the server.
        """
        if request.params.token == "backgroundIndexProgress":
            # Send a null response to acknowledge the request
            await self._clangd.respond_to_server(request.id, None)
            if not self._is_indexing:
                logging.debug("Background indexing started.")
                self._indexing_token = request.params.token
                self._is_indexing = True
                self._indexing_finished_event.clear()

    async def _handle_progress_notification(
        self, notification: ProgressNotificationMessage
    ):
        """
        Handles $/progress notifications from the server.
        """
        if notification.params.token == self._indexing_token:
            if isinstance(notification.params.value, WorkDoneProgressEnd):
                logging.debug("Background indexing finished.")
                self._indexing_finished_event.set()
