from pathlib import Path
from typing import Any, List

import cattrs
from attr import Factory
from attrs import define

from .lsp.messages import Diagnostic

__all__ = ["LineFilter"]


@define
class LineRange:
    start: int
    end: int

    def intersect_with(self, other: "LineRange") -> bool:
        return max(self.start, other.start) <= min(self.end, other.end)


@cattrs.register_structure_hook
def range_structure_hook(val: List[int], _: type) -> LineRange:
    if len(val) != 2:
        raise ValueError("Range must be a list of two integers.")
    return LineRange(val[0], val[1])


@cattrs.register_unstructure_hook
def range_unstructure_hook(obj: LineRange) -> List[int]:
    return [obj.start, obj.end]


@define
class FileLineFilter:
    """
    Filters diagnostics in line ranges of a specific file
    """

    name: Path
    """
    File path
    """

    lines: List[LineRange] = Factory(list)
    """
    List of inclusive line ranges where diagnostics will be emitted

    If empty, all diagnostics will be emitted
    """

    def matches_file(self, file: Path) -> bool:
        return str(file.resolve()).endswith(str(self.name))

    def matches_range(self, start: int, end: int) -> bool:
        return not self.lines or any(
            LineRange(start, end).intersect_with(line_range)
            for line_range in self.lines
        )


@define
class LineFilter:
    """
    Filters diagnostics by line ranges.
    This is meant to be compatible with clang-tidy --line-filter syntax.
    """

    file_line_filters: List[FileLineFilter]
    """
    The format of the list is a JSON array of objects:
      [
        {"name":"file1.cpp","lines":[[1,3],[5,7]]},
        {"name":"file2.h"}
      ]
    """

    def passes_line_filter(self, file: Path, diagnostic: Diagnostic) -> bool:
        """
        Check if a diagnostic passes the line filter.

        @see https://github.com/llvm/llvm-project/blob/980d66caae62de9b56422a2fdce3f535c2ab325f/clang-tools-extra/clang-tidy/ClangTidyDiagnosticConsumer.cpp#L463-L479
        """
        if not self.file_line_filters:
            return True
        first_match_filter = next(
            (f for f in self.file_line_filters if f.matches_file(file)),
            None,
        )
        if first_match_filter is None:
            return False

        # EXTRA: keep clang-format diagnostics unfiltered
        if diagnostic.source is not None and diagnostic.source == "clang-format":
            return True
        # EXTRA: filter out clang-tidy diagnostics without source and code
        if diagnostic.source is None and diagnostic.code is None:
            return False

        return first_match_filter.matches_range(
            diagnostic.range.start.line + 1, diagnostic.range.end.line + 1
        )


@cattrs.register_structure_hook
def line_filter_structure_hook(val: List[Any], _: type) -> LineFilter:
    return LineFilter([cattrs.structure(f, FileLineFilter) for f in val])


@cattrs.register_unstructure_hook
def line_filter_unstructure_hook(obj: LineFilter) -> List[FileLineFilter]:
    return [cattrs.unstructure(f) for f in obj.file_line_filters]
