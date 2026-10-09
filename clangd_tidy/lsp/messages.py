"""
This module defines the data structures for the Language Server Protocol (LSP)
messages used in this project.

The messages are defined using `attrs` classes and are structured as follows:
- Base classes like `Message`, `AbstractResponseMessage`, etc.
- Concrete message types for specific requests, responses, and notifications.
- `Union` types like `LspMessage` to represent any possible message.
- A `cattrs` structure hook to automatically deserialize messages into the
    correct type.
"""

from __future__ import annotations
from abc import ABC
from enum import Enum, unique
from functools import total_ordering
import pathlib
from typing import Any, Dict, List, Optional, Tuple, Union, Literal
from urllib.parse import unquote, urlparse

import cattrs
from attrs import Factory, define
from typing_extensions import Self


def uri_to_path(uri: str) -> pathlib.Path:
    path = unquote(urlparse(uri).path)
    # On Windows, file URIs like "file:///c:/path" get parsed as "/c:/path"
    # Remove the leading slash if it's followed by a drive letter
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return pathlib.Path(path)


@define
class Message:
    jsonrpc: str = "2.0"


@unique
class RequestMethod(Enum):
    INITIALIZE = "initialize"
    SHUTDOWN = "shutdown"
    FORMATTING = "textDocument/formatting"
    CODE_ACTION = "textDocument/codeAction"
    EXECUTE_COMMAND = "workspace/executeCommand"


@unique
class NotificationMethod(Enum):
    INITIALIZED = "initialized"
    EXIT = "exit"
    DID_OPEN = "textDocument/didOpen"


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
    params: Dict[str, Any] = Factory(dict[str, Any])


@define
class ResponseError:
    code: int
    message: str
    data: Optional[Dict[str, Any]] = None


@define(kw_only=True)
class AbstractResponseMessage(Message):
    id: int
    result: Any = None
    error: Optional[ResponseError] = None


@define(kw_only=True)
class InitializeParams(Params):
    processId: Optional[int] = None
    rootUri: Optional[str] = None
    initializationOptions: Any = None
    capabilities: Optional[ClientCapabilities] = None
    workspaceFolders: List[WorkspaceFolder]


@define(kw_only=True)
class InitializeResponseMessage(AbstractResponseMessage):
    params: InitializeParams


@define
class AbstractServerRequest(ABC):
    id: int
    method: Any  # Let the concrete subclasses define the method type
    params: Any  # Let the concrete subclasses define the params type


@define(kw_only=True)
class AbstractNotificationMessage(Message, ABC):
    method: Any  # Let the concrete subclasses define the method type
    params: Any  # Let the concrete subclasses define the params type


@define
class PublishDiagnosticsParams(Params):
    uri: str
    diagnostics: List[Diagnostic]
    version: Optional[int] = None


@define(kw_only=True)
class PublishDiagnosticsNotificationMessage(AbstractNotificationMessage):
    method: Literal["textDocument/publishDiagnostics"] = (
        "textDocument/publishDiagnostics"
    )
    params: PublishDiagnosticsParams


@define
class ProgressParams:
    token: str
    value: WorkDoneProgressValue


@define(kw_only=True)
class ProgressNotificationMessage(AbstractNotificationMessage):
    method: Literal["$/progress"] = "$/progress"
    params: ProgressParams


@define
class ApplyWorkspaceEditParams:
    edit: WorkspaceEdit


@define
class ApplyWorkspaceEditRequest(AbstractServerRequest):
    params: ApplyWorkspaceEditParams
    method: Literal["workspace/applyEdit"] = "workspace/applyEdit"


@define
class WorkDoneProgressCreateParams:
    token: str


@define
class WorkDoneProgressCreateRequest(AbstractServerRequest):
    params: WorkDoneProgressCreateParams
    method: Literal["window/workDoneProgress/create"] = "window/workDoneProgress/create"


NotificationMessage = Union[
    PublishDiagnosticsNotificationMessage,
    ProgressNotificationMessage,
]
ServerRequest = Union[
    WorkDoneProgressCreateRequest,
    ApplyWorkspaceEditRequest,
]
ResponseMessage = Union[AbstractResponseMessage,]
LspMessage = Union[ResponseMessage, NotificationMessage, ServerRequest]


@define
class WorkspaceFolder:
    uri: str
    name: str


@define
class CodeActionKindValueSet:
    valueSet: List[str] = Factory(
        lambda: [
            "quickfix",
            "refactor",
        ]
    )


@define
class CodeActionLiteralSupport:
    codeActionKind: CodeActionKindValueSet = Factory(CodeActionKindValueSet)


@define
class CodeActionClientCapabilities:
    dynamicRegistration: bool = False
    codeActionLiteralSupport: Optional[CodeActionLiteralSupport] = Factory(
        CodeActionLiteralSupport
    )
    isPreferredSupport: bool = True
    disabledSupport: bool = True


@define
class TextDocumentClientCapabilities:
    codeAction: Optional[CodeActionClientCapabilities] = None


@define
class WindowClientCapabilities:
    workDoneProgress: bool = False


@define
class WorkspaceClientCapabilities:
    applyEdit: bool = False


@define
class ClientCapabilities:
    textDocument: Optional[TextDocumentClientCapabilities] = None
    window: Optional[WindowClientCapabilities] = None
    workspace: Optional[WorkspaceClientCapabilities] = None
    # LSP 3.17 standard position encoding (preferred)
    positionEncodings: Optional[List[str]] = None
    # Deprecated clangd extension (for older clangd versions)
    offsetEncoding: Optional[List[str]] = None


@define
class TextDocumentItem:
    uri: str
    languageId: LanguageId
    version: int
    text: str


@define
class DidOpenTextDocumentParams(Params):
    textDocument: TextDocumentItem


@define(frozen=True)
class Position:
    line: int
    character: int


@define(frozen=True)
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


@define(frozen=True)
class CodeDescription:
    href: str


@define(frozen=True)
class Diagnostic:
    range: Range
    message: str
    severity: Optional[DiagnosticSeverity] = None
    code: Any = None
    codeDescription: Optional[CodeDescription] = None
    source: Optional[str] = None
    tags: Optional[Tuple[Any, ...]] = None
    relatedInformation: Optional[Tuple[Any, ...]] = None
    data: Any = None
    uri: Optional[str] = None  # not in LSP spec, but clangd sends it
    codeActions: Optional[Tuple[CodeAction, ...]] = None


@define
class WorkDoneProgressParams(Params):
    workDoneToken: Any = None


@define
class TextDocumentIdentifier:
    uri: str


@define(kw_only=True)
class DocumentFormattingParams(WorkDoneProgressParams):
    textDocument: TextDocumentIdentifier
    options: Dict[str, Any] = Factory(dict[str, Any])


@define
class TextEdit:
    range: Range
    newText: str


@define
class WorkspaceEdit:
    changes: Optional[Dict[str, List[TextEdit]]] = None


@define
class Command:
    title: str
    arguments: List[Any]
    command: str


@define
class CodeAction:
    title: str
    kind: Optional[str] = None
    diagnostics: Optional[List[Diagnostic]] = None
    edit: Optional[WorkspaceEdit] = None
    command: Optional[Command] = None
    isPreferred: Optional[bool] = None
    disabled: Optional[Dict[str, str]] = None


@define
class CodeActionContext:
    diagnostics: List[Diagnostic]
    only: Optional[List[str]] = None


@define
class CodeActionParams(Params):
    textDocument: TextDocumentIdentifier
    range: Range
    context: CodeActionContext


@define
class ExecuteCommandParams(Params):
    command: str
    arguments: List[Any]


# Work Done Progress structures for background indexing
@define
class WorkDoneProgressBegin:
    kind: Literal["begin"] = "begin"
    title: str = ""
    cancellable: bool = False
    message: Optional[str] = None
    percentage: Optional[int] = None


@define
class WorkDoneProgressReport:
    kind: Literal["report"] = "report"
    cancellable: bool = False
    message: Optional[str] = None
    percentage: Optional[int] = None


@define
class WorkDoneProgressEnd:
    kind: Literal["end"] = "end"
    message: Optional[str] = None


WorkDoneProgressValue = Union[
    WorkDoneProgressBegin, WorkDoneProgressReport, WorkDoneProgressEnd
]


@cattrs.register_structure_hook
def disambiguate_lsp_message(obj: Dict[str, Any], type_: Any) -> LspMessage:
    """
    A cattrs structure hook to deserialize a dictionary into the correct
    LSP message type.

    This is necessary because LSP messages are ambiguous and can only be
    differentiated by the presence of "id" and "method" fields, which is not
    directly supported by cattrs.
    """
    # We're using Unions which are supported by cattrs, but not handled by cattrs type
    # annotations, so we disable the corresponding type checks.
    if "method" in obj and "id" in obj:
        return cattrs.structure(obj, ServerRequest)  # type: ignore[arg-type]
    elif "method" in obj:
        return cattrs.structure(obj, NotificationMessage)  # type: ignore[arg-type]
    elif "id" in obj:
        return cattrs.structure(obj, ResponseMessage)  # type: ignore[arg-type]
    else:
        raise ValueError("Unknown LSP message type")
