from enum import Enum, unique
from functools import total_ordering
from typing import Any

from attrs import Factory, define
from typing_extensions import Self


@define
class Message:
    jsonrpc: str = "2.0"


@unique
class RequestMethod(Enum):
    INITIALIZE = "initialize"
    SHUTDOWN = "shutdown"
    FORMATTING = "textDocument/formatting"


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
    params: dict[str, Any] = Factory(dict)


@define
class ResponseError:
    code: int
    message: str
    data: dict[str, Any] | None = None


@define(kw_only=True)
class ResponseMessage(Message):
    id: int
    result: Any = None
    error: ResponseError | None = None


@define(kw_only=True)
class LspNotificationMessage(Message):
    method: NotificationMethod
    params: dict[str, Any] = Factory(dict)


@define
class WorkspaceFolder:
    uri: str
    name: str


@define
class InitializeParams(Params):
    processId: int | None = None
    rootUri: str | None = None
    initializationOptions: Any = None
    capabilities: Any = None
    workspaceFolders: list[WorkspaceFolder] = Factory(list)


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

    def __lt__(self, other: Self) -> bool:
        if self.__class__ is other.__class__:
            return self.value < other.value
        return NotImplemented


@define
class CodeDescription:
    href: str


@define
class Diagnostic:
    range: Range
    message: str
    severity: DiagnosticSeverity | None = None
    code: Any = None
    codeDescription: CodeDescription | None = None
    source: str | None = None
    tags: list[Any] | None = None
    relatedInformation: list[Any] | None = None
    data: Any = None
    uri: str | None = None  # not in LSP spec, but clangd sends it


@define
class PublishDiagnosticsParams(Params):
    uri: str
    diagnostics: list[Diagnostic]
    version: int | None = None


@define
class WorkDoneProgressParams(Params):
    workDoneToken: Any = None


@define
class TextDocumentIdentifier:
    uri: str


@define(kw_only=True)
class DocumentFormattingParams(WorkDoneProgressParams):
    textDocument: TextDocumentIdentifier
    options: dict[str, Any] = Factory(dict)
