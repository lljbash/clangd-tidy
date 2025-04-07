from enum import Enum, unique
from functools import total_ordering
from typing import Any, List, Optional

from attrs import Factory, define


@define
class Message:
    jsonrpc: str = "2.0"


@unique
class RequestMethod(Enum):
    INITIALIZE = "initialize"
    SHUTDOWN = "shutdown"


@unique
class NotificationMethod(Enum):
    INITIALIZED = "initialized"
    EXIT = "exit"
    DID_OPEN = "textDocument/didOpen"
    PUBLISH_DIAGNOSTICS = "textDocument/publishDiagnostics"


@unique
class LanguageId(Enum):
    CPP = "cpp"


@define
class Params:
    pass


@define(kw_only=True)
class RequestMessage(Message):
    id: int
    method: RequestMethod
    params: Params


@define
class ResponseError:
    code: int
    message: str
    data: Optional[dict] = None


@define(kw_only=True)
class ResponseMessage(Message):
    id: int
    result: Optional[dict] = None
    error: Optional[ResponseError] = None


@define(kw_only=True)
class LspNotificationMessage(Message):
    method: NotificationMethod
    params: dict = Factory(dict)


@define
class WorkspaceFolder:
    uri: str
    name: str


@define
class InitializeParams(Params):
    processId: Optional[int] = None
    rootUri: Optional[str] = None
    initializationOptions: Any = None
    capabilities: Any = None
    workspaceFolders: List[WorkspaceFolder] = Factory(list)


@define
class TextDocumentItem:
    uri: str
    languageId: LanguageId
    version: int
    text: str


@define
class DidOpenTextDocumentParams(Params):
    textDocument: TextDocumentItem


@define
class Position:
    line: int
    character: int


@define
class Range:
    start: Position
    end: Position


@unique
@total_ordering
class DiagnosticSeverity(Enum):
    ERROR = 1
    WARNING = 2
    INFORMATION = 3
    HINT = 4

    def __lt__(self, other) -> bool:
        if self.__class__ is other.__class__:
            return self.value < other.value
        return NotImplemented


@define
class Diagnostic:
    range: Range
    message: str
    severity: Optional[DiagnosticSeverity] = None
    code: Any = None
    codeDescription: Any = None
    source: Optional[str] = None
    tags: Optional[List[Any]] = None
    relatedInformation: Optional[List[Any]] = None
    data: Any = None
    uri: Optional[str] = None  # not in LSP spec, but clangd sends it


@define
class PublishDiagnosticsParams(Params):
    uri: str
    diagnostics: List[Diagnostic]
    version: Optional[int] = None
