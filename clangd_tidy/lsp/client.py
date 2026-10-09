import asyncio
import itertools
import logging
from typing import Callable, Dict, Optional, Any, cast

import cattrs

from .messages import (
    AbstractNotificationMessage,
    AbstractResponseMessage,
    LspMessage,
    NotificationMethod,
    Params,
    RequestMessage,
    RequestMethod,
)
from .rpc import RpcEndpointAsync


__all__ = ["ClientAsync"]


class ClientAsync:
    """A pure LSP protocol client.

    Handles low-level protocol concerns:
    - Request/response correlation
    - Message serialization/deserialization
    - Protocol-level send/recv

    Does NOT handle application-level concerns like progress tracking
    or workspace edit correlation - those belong in higher layers.
    """

    def __init__(self, rpc: RpcEndpointAsync):
        self._rpc = rpc
        self._id = itertools.count()
        self._logger = logging.getLogger(__name__)
        self._pending_requests: Dict[int, asyncio.Future[AbstractResponseMessage]] = {}

    async def request(self, method: RequestMethod, params: Params = Params()) -> int:
        """
        Send a request and return the request ID.

        The caller can use this ID to correlate the request with a response
        received later. However, for most cases, it is easier to use
        `request_and_await_response`.
        """
        id = next(self._id)
        message = RequestMessage(
            id=id, method=method, params=cattrs.unstructure(params)
        )
        await self._rpc.send(cattrs.unstructure(message))
        return id

    async def request_and_await_response(
        self,
        method: RequestMethod,
        params: Params = Params(),
        request_id_cb: Optional[Callable[[int], Any]] = None,
    ) -> AbstractResponseMessage:
        """Send a request and wait for the corresponding response.

        Optionally calls request_id_cb with the request ID once the request is sent.
        Args:
            method: The LSP request method to send.
            params: The parameters for the request.
            request_id_cb: Optional callback to receive the request ID.
        Returns:
            The result from the response message.
        """

        request_id = await self.request(method, params)

        request_future: asyncio.Future[AbstractResponseMessage] = (
            asyncio.get_event_loop().create_future()
        )
        self._pending_requests[request_id] = request_future

        if request_id_cb is not None:
            request_id_cb(request_id)
        return await request_future

    async def notify(
        self, method: NotificationMethod, params: Params = Params()
    ) -> None:
        message = AbstractNotificationMessage(
            method=method, params=cattrs.unstructure(params)
        )
        await self._rpc.send(cattrs.unstructure(message))

    async def respond(self, id: int, result: Any = None) -> None:
        """Send a response to a server request."""
        response = AbstractResponseMessage(id=id, result=result)
        await self._rpc.send(cattrs.unstructure(response))

    async def shutdown(self) -> None:
        """Clean up the client, cancelling any pending requests."""
        for future in self._pending_requests.values():
            future.cancel()
            try:
                await future
            except asyncio.CancelledError:
                pass
        self._pending_requests.clear()

    async def recv(
        self,
    ) -> LspMessage:
        """Receive and parse the next message from the server.

        LSP is bidirectional: both client and server can send requests/notifications.

        Returns:
            LspMessage: The received message, which can be one of:
            - ResponseMessage: Response to our request (we sent request, got response)
            - NotificationMessage: Notification from server (has method, no id)
            - AbstractServerRequest: Request from server (has method AND id, needs
              response)

        Message classification:
        - has "id" but no "method": ResponseMessage
        - has "method" and "id": AbstractServerRequest (needs response)
        - has "method" but no "id": NotificationMessage (no response)
        """
        content = await self._rpc.recv()
        self._logger.debug(f"Received message: {content}")

        message: LspMessage = cast(LspMessage, cattrs.structure(content, LspMessage))  # type: ignore[arg-type]

        if isinstance(message, AbstractResponseMessage):
            # Pair the response with its request if we track it
            pending_request = self._pending_requests.pop(message.id, None)
            if pending_request is not None:
                pending_request.set_result(message)

        return message
