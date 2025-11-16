import logging
import pathlib
from typing import Collection, List

from .base import BaseFlow

from ..diagnostic_formatter import DiagnosticCollection
from ..event_bus import EventBus
from ..lsp import ClangdAsync
from ..lsp.messages import (
    Diagnostic,
    Position,
    Range,
)


class FormattingFlow(BaseFlow):
    """
    Manages the formatting of a collection of files.

    This class orchestrates the entire formatting process, including:
    - Spawning formatting flows for each file.
    - Running `clang-format` to get formatting diagnostics.

    Simplified flow description:
    ┌─────────────────────────────────┐
    │ Main Flow (run())               │
    ├─────────────────────────────────┤
    │ 1. spawn_task per file          │
    │ 2. _run_formatting_flow(file)   │
    │ 3. send textDocument/formatting │
    │ 4. await response               │
    │ 5. convert TextEdits to         │
    │    Diagnostics                  │
    │ 6. return diagnostics           │
    └─────────────────────────────────┘
    """

    def __init__(
        self,
        clangd_client: ClangdAsync,
        event_bus: EventBus,
        files: Collection[pathlib.Path],
    ):
        super().__init__(clangd_client, event_bus)
        self._files = set(files)

    async def _run_formatting_flow(self, path: pathlib.Path) -> List[Diagnostic]:
        """
        Runs clang-format on a file and returns a list of formatting diagnostics.

        1. --> textDocument/formatting (Request formatting for the file.)
        2. <-- response (response.result contains a list of `TextEdit` objects.)
        3. Convert `TextEdit` objects to `Diagnostic` objects.
        """
        try:
            response = await self._clangd.formatting(path)
            if response.result:
                return [
                    Diagnostic(
                        range=Range(start=Position(0, 0), end=Position(0, 0)),
                        message="File does not conform to the formatting rules (run `clang-format` to fix)",
                        source="clang-format",
                    )
                ]
            else:
                return []
        except Exception as e:
            logging.warning(f"Formatting failed for '{path}': {e}", exc_info=True)
            raise

    async def run(self) -> None:
        for file_path in self._files:
            if file_path in self._futures and self._futures[file_path].done():
                # Skip already completed formatting flows
                continue
            self._spawn_task(file_path, self._run_formatting_flow(file_path))

    def get_formatting_diagnostics(self) -> DiagnosticCollection:
        """Get all collected formatting diagnostics from completed file flows."""
        diagnostics: DiagnosticCollection = {}
        for path, future in self._futures.items():
            if not future.done():
                raise RuntimeError(f"Formatting for '{path}' is not yet complete")
            file_diagnostics = future.result()
            if file_diagnostics:
                diagnostics[path] = file_diagnostics
        return diagnostics
