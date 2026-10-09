import asyncio
from dataclasses import dataclass
import logging
import pathlib
from typing import Any, Collection, Dict, List, Tuple

from .diagnostics import DiagnosticsFlow

from ..event_bus import EventBus
from ..lsp import ClangdAsync
from ..lsp.messages import (
    ApplyWorkspaceEditRequest,
    ClientCapabilities,
    CodeAction,
    CodeActionClientCapabilities,
    Diagnostic,
    TextDocumentClientCapabilities,
    WorkspaceClientCapabilities,
    WorkspaceEdit,
)
from ..progress import IndexingProgressTracker

FixitKey = Tuple[pathlib.Path, str, int, int, int, int]


@dataclass
class RejectedEdit:
    """Marker class indicating that a workspace edit was rejected and should not be applied."""

    error: str


class DiagnosticsWithFixesFlow(DiagnosticsFlow):
    """
    Extends DiagnosticsFlow to automatically apply fixes for all diagnostics.

    This flow orchestrates the complete fix application process by:
    - Collecting diagnostics for each file (inherited from DiagnosticsFlow)
    - Waiting for clangd background indexing to complete (critical for renames)
    - Requesting code actions for each fixable diagnostic
    - Executing fixes via direct edits or workspace commands
    - Handling workspace/applyEdit server requests from clangd

    Flow diagram (per file):
    ┌──────────────────────────────────────────────────────────────────────────┐
    │ File Task (limited concurrency)                                          │
    ├──────────────────────────────────────────────────────────────────────────┤
    │ 1. Get diagnostics (inherited)                                           │
    │ 2. Filter diagnostics with "(fix available)" in message                  │
    │ 3. Spawn fix task per fixable diagnostic (unlimited concurrency)         │
    │ 4. Gather all fix results                                                │
    │ 5. Return WorkspaceEdits                                                 │
    └──────────────────────────────────────────────────────────────────────────┘

    Fix task flow (per diagnostic, runs concurrently):
    ┌─────────────────────────────────┬────────────────────────────────────────┐
    │ Fix Task (_run_fix_flow)        │ Event Handlers / Responses             │
    ├─────────────────────────────────┼────────────────────────────────────────┤
    │ 1. Wait for indexing complete   │                                        │
    │ 2. Request code action          │                                        │
    │    → textDocument/codeAction    │ ← Response with CodeAction[]           │
    │ 3. Select best fix action       │                                        │
    │                                 │                                        │
    │ Case A: Direct edit available   │                                        │
    │ ──────────────────────────────────────────────────────────────────────── │
    │ 4a. Return edit immediately     │                                        │
    │                                 │                                        │
    │ Case B: Command (e.g., rename)  │                                        │
    │ ──────────────────────────────────────────────────────────────────────── │
    │ 4b. Execute command             │                                        │
    │     → workspace/executeCommand  │ ← Response (success/error)             │
    │ 5b. Create future for edit      │                                        │
    │ 6b. Await workspace edit        │ ← workspace/applyEdit (server request) │
    │     (blocked)                   │ → _apply_workspace_edit_cb()           │
    │                                 │ → Match range, resolve future          │
    │                                 │ → Respond {"applied": true}            │
    │ 7b. Return edit (unblocked)     │                                        │
    └─────────────────────────────────┴────────────────────────────────────────┘

    Concurrency notes:
    - File tasks are limited by semaphore to avoid overwhelming clangd
    - Fix tasks within a file run with unlimited concurrency to prevent deadlock
    - Commands may trigger cross-file edits (e.g., rename across headers)

    Future keys:
    - (path,): File-level fix processing task
    - (path, "diagnostics"): Diagnostics collection (inherited)
    - (path, diagnostic): Complete fix flow for this diagnostic
    - (path, diagnostic, "command"): Command execution result (workspace edit)

    Known limitations:
    - Cross-file fixes may be missed for files not in the compilation database.
      Clangd can only provide cross-file fixes correctly for files it has indexed or
      opened. Only files in the compilation database are guaranteed to be indexed
      automatically.
      This sometimes works if the file is opened directly, but we process files in
      non-deterministic order, which can lead to inconsistent results. To avoid this,
      ensure all source files are listed in the compilation database.
    """

    def __init__(
        self,
        clangd_client: ClangdAsync,
        event_bus: EventBus,
        files: Collection[pathlib.Path],
        indexing_progress_tracker: IndexingProgressTracker,
    ):
        super().__init__(clangd_client, event_bus, files)
        self._indexing_progress_tracker = indexing_progress_tracker

        # Pending commands mapped by request ID - used to correlate ApplyWorkspaceEdit
        # requests to diagnostics
        self._commands: Dict[
            int,
            Tuple[
                asyncio.Future[WorkspaceEdit | RejectedEdit], Diagnostic, pathlib.Path
            ],
        ] = {}

        self._event_bus.subscribe(
            ApplyWorkspaceEditRequest, self._apply_workspace_edit_cb
        )

    async def bootstrap(self):
        # If we have not finished processing fixes for some files, we need to wait for
        # their diagnostics again. For that reason we purge any existing diagnostics
        # futures for files that have incomplete fix flows.
        for key in list(self._futures.keys()):
            if self._futures[key].done():
                continue
            match key:
                case pathlib.Path() as path, Diagnostic():
                    self._futures.pop((path, "diagnostics"), None)
                case _:
                    continue
        # Also delete any _commands, they will be re-created as needed. They are tied
        # to clangd state and must not persist across restarts.
        self._commands.clear()
        await super().bootstrap()

    async def run(self):
        for path in self._files:
            if path in self._futures and self._futures[path].done():
                # Skip already completed diagnostics flows
                continue
            self._spawn_task(path, self._process_file_fixes(path))

    async def _process_file_fixes(self, path: pathlib.Path) -> List[WorkspaceEdit]:
        """
        Orchestrates all fix-it operations for a single file, running them concurrently.
        """
        # 1. Get diagnostics for the file. This also ensures the file is opened in
        # clangd, which is necessary for workspace
        diagnostics = await self._open_and_get_diagnostics(path)

        # Process all diagnostics with available fixes concurrently.
        # Note that diagnostics can contain multiple fixes, in which case the suffix
        # would be "(fixes available)". Since for these the user must pick one fix among
        # several, we currently do not attempt to auto-apply them.
        # We might have also received diag.codeActions for some diagnostics. However,
        # these inline codeActions do not handle cross-file fixes, so we always request
        # fresh code actions from clangd instead to ensure we get complete fixes.
        diags_with_fixes = [
            diag for diag in diagnostics if "(fix available)" in diag.message
        ]

        logging.debug(
            f"Found {len(diags_with_fixes)} diagnostics with fixes for '{path}'."
        )
        fix_tasks: List[asyncio.Future[WorkspaceEdit | RejectedEdit]] = []
        for diag in diags_with_fixes:
            # Run fix flow for each diagnostic that has a fix
            if ((path, diag) in self._futures) and self._futures[(path, diag)].done():
                continue
            # Spawn a new fix task. We do not limit concurrency here as we are already
            # limiting it at the file level. This avoids a possible deadlock when
            # processing many files - if all file-level tasks were waiting for
            # concurrency slots, no fix tasks could proceed to complete them.
            task: asyncio.Future[WorkspaceEdit | RejectedEdit] = self._spawn_task(
                (path, diag), self._run_fix_flow(path, diag), limited=False
            )
            fix_tasks.append(task)

        if fix_tasks:
            edits = await asyncio.gather(*fix_tasks, return_exceptions=True)
            # Filter out exceptions and rejected edits and log them
            valid_edits: List[WorkspaceEdit] = []
            for edit in edits:
                if isinstance(edit, WorkspaceEdit):
                    valid_edits.append(edit)
                elif isinstance(edit, RejectedEdit):
                    pass
                elif isinstance(edit, Exception):
                    logging.exception(
                        f"Unexpected error during fix application: {edit}",
                        exc_info=edit,
                    )
                elif isinstance(edit, asyncio.CancelledError):
                    logging.warning(
                        f"Fix application was cancelled and will be retried: {edit}",
                    )
                else:
                    raise RuntimeError(
                        f"Unexpected result type from fix task: {type(edit)}"
                    )
            return valid_edits
        return []

    async def _apply_workspace_edit_cb(self, request: ApplyWorkspaceEditRequest):
        """
        Callback for the `workspace/applyEdit` request from the server.

        This method finds the command that triggered this edit request by matching
        the file path and diagnostic range, and then resolves the corresponding
        future with the edit.
        """
        for _, (cmd_future, diag, path) in self._commands.items():
            assert request.params.edit.changes is not None
            for edit in request.params.edit.changes.get(path.as_uri(), []):
                if edit.range == diag.range:
                    cmd_future.set_result(request.params.edit)
                    logging.debug(
                        f"  Added workspace edit for diagnostic: {diag.message}"
                    )
                    await self._clangd.respond_to_server(request.id, {"applied": True})
                    # We must respond to the request, otherwise clangd will hang.
                    break
            else:
                continue
            break
        else:
            logging.warning(
                "No matching execute command found for ApplyWorkspaceEditRequest."
            )

    async def _run_fix_flow(
        self, path: pathlib.Path, diag: Diagnostic
    ) -> WorkspaceEdit | RejectedEdit:
        """Applies a single fix for a given diagnostic."""
        try:
            await self._indexing_progress_tracker.wait_for_indexing_to_finish()

            logging.debug(f"Requesting code action for: {diag.message}")
            code_action_response = await self._clangd.code_action(
                path,
                diag.range.start,
                diag.range.end,
                [diag],
            )

            # Check for error response
            if code_action_response.error:
                raise RuntimeError(
                    f"Code action request failed: {code_action_response.error.message}"
                )

            code_actions: List[CodeAction] = code_action_response.result
            if not code_actions:
                raise RuntimeError(f"No code actions returned for: {diag.message}")

            best_action = self._clangd.select_best_fix_action(
                code_actions, diag.message
            )

            if best_action.edit:
                logging.debug(f"Applied direct edit for: {diag.message}")
                return best_action.edit
            elif best_action.command:
                if (path, diag, "command") in self._futures:
                    assert self._futures[(path, diag, "command")].done()
                    logging.debug(
                        f"Command {best_action.command.command} already executed for: {diag.message}, re-using result"
                    )
                    return self._futures[(path, diag, "command")].result()

                cmd_future: asyncio.Future[WorkspaceEdit | RejectedEdit] = (
                    self._create_future((path, diag, "command"))
                )

                def execute_id_callback(request_id: int):
                    # Register the command future to be resolved when the
                    # ApplyWorkspaceEdit request arrives. The response to
                    # execute_command only arrives after we process
                    # _apply_workspace_edit_cb and execute_command blocks until then, so
                    # this ensures the future is resolved correctly.
                    self._commands[request_id] = (cmd_future, diag, path)

                response = await self._clangd.execute_command(
                    best_action.command.command,
                    best_action.command.arguments,
                    execute_id_callback,
                )
                if response.error:
                    err = (
                        f"Command execution failed for '{diag.message}'"
                        f": {response.error.message}"
                    )
                    logging.error(err)
                    # We want to log the error but continue processing other fixes. This
                    # allows us to collect as many fixes as possible in one run,
                    # recovering from individual fix failures caused by clangd
                    # limitations.
                    cmd_future.set_result(RejectedEdit(error=response.error.message))

                edit = await cmd_future
                logging.debug(f"Command executed for: {diag.message}")
                return edit
            else:
                raise RuntimeError(
                    f"No edit or command in selected action for: {diag.message}"
                )
        except Exception as e:
            logging.exception(
                f"Fix failed for '{diag.message}' at {path}:{diag.range.start.line}: {e}"
            )
            raise

    def get_edits(self) -> List[Tuple[Diagnostic, WorkspaceEdit]]:
        """Get all collected edits from completed file flows."""
        results: List[Tuple[Diagnostic, WorkspaceEdit]] = []
        for key, future in self._futures.items():
            # From all the futures, extract only the fix command futures and return their diagnostics/edits
            match key:
                case pathlib.Path() as path, Diagnostic() as diag:
                    if not future.done():
                        raise RuntimeError(
                            f"Fix for '{diag.message}' in '{path}' is not yet complete"
                        )
                    result = future.result()
                    if isinstance(result, RejectedEdit):
                        continue
                    results.append((diag, result))
                case _:
                    continue
        return results

    def add_required_capabilities(
        self, capabilities: ClientCapabilities
    ) -> ClientCapabilities:
        capabilities.offsetEncoding = ["utf-8"]
        capabilities.positionEncodings = ["utf-8"]
        capabilities.textDocument = (
            capabilities.textDocument or TextDocumentClientCapabilities()
        )
        capabilities.textDocument.codeAction = (
            capabilities.textDocument.codeAction or CodeActionClientCapabilities()
        )
        capabilities.workspace = capabilities.workspace or WorkspaceClientCapabilities()
        capabilities.workspace.applyEdit = True
        return capabilities

    def check_clangd_capabilities(self, init_result: Any) -> None:
        # Verify server uses UTF-8 encoding (required for our byte offset calculations)
        # Check both LSP 3.17 standard field and deprecated clangd extension field
        capabilities = init_result.get("capabilities", {})
        encoding = capabilities.get("positionEncoding") or init_result.get(
            "offsetEncoding"
        )
        if encoding != "utf-8":
            raise RuntimeError(
                f"clangd did not accept UTF-8 encoding (got: {encoding!r}). "
                f"This tool requires UTF-8 for correct byte offset handling. "
                f"Please use clangd version 9+ which supports UTF-8 offsets."
            )

        # Check that server confirmes support for 'clangd.applyRename' command and
        # code actions
        commands = capabilities.get("executeCommandProvider", {}).get("commands", [])
        if "clangd.applyRename" not in commands:
            raise RuntimeError(
                f"clangd does not support 'clangd.applyRename' command. "
                f"This tool requires this command to apply fixes. "
            )
        code_action_kinds = capabilities.get("codeActionProvider", {}).get(
            "codeActionKinds", []
        )
        if not code_action_kinds:
            raise RuntimeError(
                f"clangd does not support any code actions. "
                f"This tool requires code actions to apply fixes. "
            )
