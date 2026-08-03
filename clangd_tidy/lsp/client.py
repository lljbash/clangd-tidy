import itertools
from dataclasses import dataclass

import cattrs

from .messages import (
    LspNotificationMessage,
    NotificationMethod,
    Params,
    RequestMessage,
    RequestMethod,
    ResponseMessage,
)
from .rpc import RpcEndpointAsync

__all__ = ["ClientAsync", "RequestResponsePair"]


@dataclass
class RequestResponsePair:
    request: RequestMessage
    response: ResponseMessage


class ClientAsync:
    def __init__(self, rpc: RpcEndpointAsync):
        self._rpc = rpc
        self._id = itertools.count()
        self._requests: dict[int, RequestMessage] = {}

    async def request(
        self, method: RequestMethod, params: Params | None = None
    ) -> None:
        if params is None:
            params = Params()
        id = next(self._id)
        message = RequestMessage(
            id=id, method=method, params=cattrs.unstructure(params)
        )
        self._requests[id] = message
        await self._rpc.send(cattrs.unstructure(message))

    async def notify(
        self, method: NotificationMethod, params: Params | None = None
    ) -> None:
        if params is None:
            params = Params()
        message = LspNotificationMessage(
            method=method, params=cattrs.unstructure(params)
        )
        await self._rpc.send(cattrs.unstructure(message))

    async def recv(self) -> RequestResponsePair | LspNotificationMessage:
        content = await self._rpc.recv()
        if "method" in content:
            return cattrs.structure(content, LspNotificationMessage)
        else:
            resp = cattrs.structure(content, ResponseMessage)
            req = self._requests.pop(resp.id)
            return RequestResponsePair(request=req, response=resp)
