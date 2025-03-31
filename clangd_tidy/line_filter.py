from pathlib import Path
from typing import Dict, List

from attrs import define

from .diagnostic_formatter import DiagnosticCollection
from .lsp.messages import Diagnostic

__all__ = ["LineFilter"]


@define
class LineRange:
    start: int
    end: int


@define
class FileLineFilter:
    """
    Filters diagnostics in line ranges of a specific file
    """

    line_ranges: List[LineRange]
    """
    List of inclusive line ranges where diagnostics will be emitted
    """

    def allows(self, start: int, end: int) -> bool:
        return any(
            self._interval_intersect(line_range, LineRange(start, end))
            for line_range in self.line_ranges
        )

    @staticmethod
    def _interval_intersect(range1: LineRange, range2: LineRange) -> bool:
        return max(range1.start, range2.start) <= min(range1.end, range2.end)


@define
class LineFilter:
    """
    Filters diagnostics by line ranges.
    This is meant to be compatible with clang-tidy --line-filter syntax.
    """

    file_line_filters: Dict[Path, FileLineFilter]
    """
    A mapping of file paths (resolved) to FileLineFilter instances
    """

    def _allows(self, file: Path, start: int, end: int) -> bool:
        file_line_filter = self.file_line_filters.get(file)
        return file_line_filter is not None and file_line_filter.allows(start, end)

    def _filter_diagnostics(
        self, file: Path, diagnostics: List[Diagnostic]
    ) -> List[Diagnostic]:
        def allow_diagnostic(diagnostic: Diagnostic) -> bool:
            return self._allows(
                file.resolve(),
                diagnostic.range.start.line + 1,
                diagnostic.range.end.line + 1,
            )

        filtered = filter(allow_diagnostic, diagnostics)
        return list(filtered)

    def filter_all_diagnostics(
        self, all_diagnostics: DiagnosticCollection
    ) -> DiagnosticCollection:
        return {
            file: self._filter_diagnostics(file, diag)
            for file, diag in all_diagnostics.items()
        }
