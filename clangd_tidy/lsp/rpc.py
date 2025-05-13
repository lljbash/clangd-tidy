import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

__all__ = ["RpcEndpointAsync"]


@dataclass
class ProtocolHeader:
    content_length: Optional[int] = None
    content_type: Optional[str] = None
    complete: bool = False


class Protocol:
    _HEADER_SEP = "\r\n"
    _HEADER_CONTENT_SEP = _HEADER_SEP * 2
    _LEN_HEADER = "Content-Length: "
    _TYPE_HEADER = "Content-Type: "

    @classmethod
    def encode(cls, data: Dict[str, Any]) -> bytes:
        content = json.dumps(data)
        header = f"{cls._LEN_HEADER}{len(content.encode())}"
        message = f"{header}{cls._HEADER_CONTENT_SEP}{content}"
        return message.encode()

    @classmethod
    def parse_header(
        cls, header_line_bin: bytes, header_to_update: ProtocolHeader
    ) -> None:
        header_line = header_line_bin.decode()
        if not header_line.endswith(cls._HEADER_SEP):
            raise ValueError("Invalid header end")
        header_line = header_line[: -len(cls._HEADER_SEP)]
        if not header_line:
            header_to_update.complete = True
        elif header_line.startswith(cls._LEN_HEADER):
            try:
                header_to_update.content_length = int(
                    header_line[len(cls._LEN_HEADER) :]
                )
            except ValueError:
                raise ValueError(f"Invalid Content-Length header field: {header_line}")
        elif header_line.startswith(cls._TYPE_HEADER):
            header_to_update.content_type = header_line[len(cls._TYPE_HEADER) :]
        else:
            raise ValueError(f"Unknown header: {header_line}")


class RpcEndpointAsync:
    def __init__(
        self, in_stream: asyncio.StreamReader, out_stream: asyncio.StreamWriter
    ):
        self._in_stream = in_stream
        self._out_stream = out_stream

    async def send(self, data: Dict[str, Any]) -> None:
        self._out_stream.write(Protocol.encode(data))
        await self._out_stream.drain()

    async def recv(self) -> Dict[str, Any]:
        header = ProtocolHeader()
        while True:
            header_line = await self._in_stream.readline()
            Protocol.parse_header(header_line, header)
            if header.complete:
                break
        if header.content_length is None:
            raise ValueError("Missing Content-Length header field")
        content = await self._in_stream.readexactly(header.content_length)
        return json.loads(content.decode())
