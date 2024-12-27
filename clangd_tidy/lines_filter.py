import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from .diagnostic_formatter import DiagnosticCollection


@dataclass
class FileLineFilter:
    """
    Filters diagnostics in line ranges of a specific file
    """

    file_name: str
    """
    File name to filter, can be any postfix of a filtered file path
    """

    line_ranges: list[tuple[int, int]] = field(default_factory=list)
    """
    List of inclusive line ranges where diagnostics will be emitted
    """

    def allows(self, file_path: str, start_line: int, end_line: int) -> bool:
        if not file_path.endswith(self.file_name):
            return False

        if len(self.line_ranges) == 0:
            return True

        for allowed_lines in self.line_ranges:
            if self.interval_intersect(allowed_lines, (start_line, end_line)):
                return True

        return False

    @staticmethod
    def interval_intersect(
        interval1: tuple[int, int], interval2: tuple[int, int]
    ) -> bool:
        a, b = interval1
        c, d = interval2

        return max(a, c) <= min(b, d)


@dataclass
class LineFilter:
    """
    Filters diagnostics by line ranges.
    This is meant to be compatible with clang-tidy --line-filter syntax.
    """

    file_line_filters: list[FileLineFilter] = field(default_factory=list)

    def allows(self, file_path: str, start_line: int, end_line: int) -> bool:
        if len(self.file_line_filters) == 0:
            return True

        for file_line_filter in self.file_line_filters:
            if file_line_filter.allows(file_path, start_line, end_line):
                return True

        return False

    def filter_diagnostics(
        self, file: str | Path, diagnostics: list[dict]
    ) -> list[dict]:
        def allow_diagnostic(diagnostic: dict) -> bool:
            return self.allows(
                str(file),
                diagnostic.range.start.line + 1,
                diagnostic.range.end.line + 1,
            )

        filtered = filter(allow_diagnostic, diagnostics)
        return list(filtered)

    def filter_all_diagnostics(
        self, all_diagnostics: DiagnosticCollection
    ) -> DiagnosticCollection:
        return {
            file: self.filter_diagnostics(file, diag)
            for file, diag in all_diagnostics.items()
        }

    @staticmethod
    def parse_line_filter(s: str) -> "LineFilter":
        try:
            line_filter = json.loads(s)
        except json.JSONDecodeError as e:
            raise argparse.ArgumentTypeError(
                f"Invalid line filter JSON string: {e=} [{s=}]"
            ) from e

        filter_list = [LineFilter._parse_file_line_filter(f) for f in line_filter]
        return LineFilter(filter_list)

    @staticmethod
    def _parse_file_line_filter(single_filter: dict) -> FileLineFilter:
        ranges = []
        lines = single_filter.get("lines", [])
        for line_range in lines:
            assert len(line_range) == 2, (
                "Line ranges should be an inclusive range"
                f" of the form [start, end] {line_range=} "
            )
            ranges.append(tuple(line_range))

        name = single_filter["name"]
        return FileLineFilter(name, ranges)
