import os
import pathlib
import re
from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional

from .lsp.messages import Diagnostic, DiagnosticSeverity

__all__ = [
    "DiagnosticCollection",
    "DiagnosticFormatter",
    "CompactDiagnosticFormatter",
    "FancyDiagnosticFormatter",
    "GithubActionWorkflowCommandDiagnosticFormatter",
]


DiagnosticCollection = Dict[pathlib.Path, List[Diagnostic]]


class DiagnosticFormatter(ABC):
    SEVERITY = {
        1: "Error",
        2: "Warning",
        3: "Information",
        4: "Hint",
    }

    def format(self, diagnostic_collection: DiagnosticCollection) -> str:
        file_outputs: List[str] = []
        for file, diagnostics in sorted(
            diagnostic_collection.items(), key=lambda fd: fd[0].as_posix()
        ):
            diagnostic_outputs = [
                o
                for o in [
                    self._format_one_diagnostic(file, diagnostic)
                    for diagnostic in diagnostics
                ]
                if o is not None
            ]
            if len(diagnostic_outputs) == 0:
                continue
            file_outputs.append(self._make_file_output(file, diagnostic_outputs))
        return self._make_whole_output(file_outputs)

    @abstractmethod
    def _format_one_diagnostic(
        self, file: pathlib.Path, diagnostic: Diagnostic
    ) -> Optional[str]:
        pass

    @abstractmethod
    def _make_file_output(
        self, file: pathlib.Path, diagnostic_outputs: Iterable[str]
    ) -> str:
        pass

    @abstractmethod
    def _make_whole_output(self, file_outputs: Iterable[str]) -> str:
        pass


class CompactDiagnosticFormatter(DiagnosticFormatter):
    def _format_one_diagnostic(
        self, file: pathlib.Path, diagnostic: Diagnostic
    ) -> Optional[str]:
        del file
        source = diagnostic.source
        severity = diagnostic.severity
        code = diagnostic.code
        extra_info = "{}{}{}".format(
            f" {source}" if source is not None else "",
            f" {self.SEVERITY[severity.value]}" if severity is not None else "",
            f" [{code}]" if code is not None else "",
        )
        line = diagnostic.range.start.line + 1
        col = diagnostic.range.start.character + 1
        message = diagnostic.message
        if source is None and code is None:
            return None
        return f"- line {line}, col {col}:{extra_info}\n{message}"

    def _make_file_output(
        self, file: pathlib.Path, diagnostic_outputs: Iterable[str]
    ) -> str:
        head = f"----- {os.path.relpath(file)} -----"
        return "\n\n".join([head, *diagnostic_outputs])

    def _make_whole_output(self, file_outputs: Iterable[str]) -> str:
        return "\n\n\n".join(file_outputs)


class GithubActionWorkflowCommandDiagnosticFormatter(DiagnosticFormatter):
    SEVERITY_GITHUB = {
        1: "error",
        2: "warning",
        3: "notice",
        4: "notice",
    }

    def __init__(self, git_root: str):
        self._git_root = git_root

    def _format_one_diagnostic(
        self, file: pathlib.Path, diagnostic: Diagnostic
    ) -> Optional[str]:
        source = diagnostic.source
        severity = diagnostic.severity
        code = diagnostic.code
        extra_info = "{}{}{}".format(
            f"{source}" if source else "",
            f" {self.SEVERITY[severity.value]}" if severity is not None else "",
            f" [{code}]" if code is not None else "",
        )
        line = diagnostic.range.start.line + 1
        end_line = diagnostic.range.end.line + 1
        col = diagnostic.range.start.character + 1
        end_col = diagnostic.range.end.character + 1
        message = diagnostic.message
        if source is None and code is None:
            return None
        if severity is None:
            severity = DiagnosticSeverity.INFORMATION
        command = self.SEVERITY_GITHUB[severity.value]
        rel_file = os.path.relpath(file, self._git_root)
        return f"::{command} file={rel_file},line={line},endLine={end_line},col={col},endCol={end_col},title={extra_info}::{message}"

    def _make_file_output(
        self, file: pathlib.Path, diagnostic_outputs: Iterable[str]
    ) -> str:
        del file
        return "\n".join(diagnostic_outputs)

    def _make_whole_output(self, file_outputs: Iterable[str]) -> str:
        head = "::group::{workflow commands}"
        tail = "::endgroup::"
        return "\n".join(["", head, *file_outputs, tail])


class FancyDiagnosticFormatter(DiagnosticFormatter):
    class Colorizer:
        class ColorSeqTty:
            ERROR = "\033[91m"
            WARNING = "\033[93m"
            INFO = "\033[96m"
            HINT = "\033[94m"
            NOTE = "\033[90m"
            GREEN = "\033[92m"
            MAGENTA = "\033[95m"
            BOLD = "\033[1m"
            ENDC = "\033[0m"
            START_LINK = "\033]8;;"
            END_LINK = "\033\\"

        class ColorSeqNoTty:
            ERROR = ""
            WARNING = ""
            INFO = ""
            HINT = ""
            NOTE = ""
            GREEN = ""
            MAGENTA = ""
            BOLD = ""
            ENDC = ""

        def __init__(self, enable_color: bool):
            self._color_seq = self.ColorSeqTty if enable_color else self.ColorSeqNoTty

        def per_severity(self, severity: int, message: str):
            if severity == 1:
                return f"{self._color_seq.ERROR}{message}{self._color_seq.ENDC}"
            if severity == 2:
                return f"{self._color_seq.WARNING}{message}{self._color_seq.ENDC}"
            if severity == 3:
                return f"{self._color_seq.INFO}{message}{self._color_seq.ENDC}"
            if severity == 4:
                return f"{self._color_seq.HINT}{message}{self._color_seq.ENDC}"
            return message

        def highlight(self, message: str):
            return f"{self._color_seq.GREEN}{message}{self._color_seq.ENDC}"

        def note(self, message: str):
            return f"{self._color_seq.NOTE}{message}{self._color_seq.ENDC}"

        def format(self, message: str):
            return f"{self._color_seq.MAGENTA}{message}{self._color_seq.ENDC}"

        def link(self, message: str, url: str):
            if not url or self._color_seq is not self.ColorSeqTty:
                return message
            return (
                f"{self._color_seq.START_LINK}{url}{self._color_seq.END_LINK}"
                f"{message}{self._color_seq.START_LINK}{self._color_seq.END_LINK}"
            )

    def __init__(self, extra_context: int, enable_color: bool):
        self._extra_context = extra_context
        self._colorizer = self.Colorizer(enable_color)

    def _colorized_severity(self, severity: int):
        return self._colorizer.per_severity(severity, self.SEVERITY[severity])

    @staticmethod
    def _prepend_line_number(line: str, lino: Optional[int]) -> str:
        LINO_WIDTH = 5
        LINO_SEP = " |  "
        lino_str = str(lino + 1) if lino is not None else ""
        return f"{lino_str:>{LINO_WIDTH}}{LINO_SEP}{line.rstrip()}\n"

    def _code_context(
        self,
        file: str,
        line_start: int,
        line_end: int,
        col_start: int,
        col_end: int,
        extra_context: Optional[int] = None,
    ) -> str:
        UNDERLINE = "~"
        UNDERLINE_START = "^"
        if extra_context is None:
            extra_context = self._extra_context

        # get context code
        with open(file, "r") as f:
            content = f.readlines()
            context_start_line = max(0, line_start - extra_context)
            context_end_line = min(len(content), line_end + extra_context + 1)
            code = content[context_start_line:context_end_line]

        context = ""
        for lino, line in enumerate(code, start=context_start_line):
            # prepend line numbers
            context += self._prepend_line_number(line, lino)

            # add diagnostic indicator line
            if lino < line_start or lino > line_end:
                continue
            line_col_start = (
                col_start if lino == line_start else len(line) - len(line.lstrip())
            )
            line_col_end = col_end if lino == line_end else len(line.rstrip())
            indicator = UNDERLINE_START if lino == line_start else UNDERLINE
            indicator = indicator.rjust(line_col_start + 1)
            indicator = indicator.ljust(line_col_end, UNDERLINE)
            indicator = self._colorizer.highlight(indicator)
            context += self._prepend_line_number(indicator, lino=None)

        return context.rstrip()

    @staticmethod
    def _diagnostic_message(
        file: str,
        line_start: int,
        col_start: int,
        severity: str,
        message: str,
        code: str,
        context: str,
    ) -> str:
        return f"{file}:{line_start + 1}:{col_start + 1}: {severity}: {message} {code}\n{context}"

    def _formatting_message(self, file: str, message: str) -> str:
        return self._colorizer.format(f"{file}: {message}")

    def _format_one_diagnostic(
        self, file: pathlib.Path, diagnostic: Diagnostic
    ) -> Optional[str]:
        rel_file = os.path.relpath(file)

        if diagnostic.source == "clang-format":
            return self._formatting_message(rel_file, diagnostic.message)

        message: str = diagnostic.message.replace(" (fix available)", "")
        message_list = [line for line in message.splitlines() if line.strip()]
        message, extra_messages = message_list[0], message_list[1:]

        if diagnostic.code is None:
            return None

        code_url = diagnostic.codeDescription.href if diagnostic.codeDescription else ""
        code = f"[{self._colorizer.link(diagnostic.code, code_url)}]"

        severity = (
            self._colorized_severity(diagnostic.severity.value)
            if diagnostic.severity is not None
            else ""
        )

        line_start = diagnostic.range.start.line
        line_end = diagnostic.range.end.line

        col_start = diagnostic.range.start.character
        col_end = diagnostic.range.end.character

        context = self._code_context(rel_file, line_start, line_end, col_start, col_end)

        fancy_output = self._diagnostic_message(
            rel_file, line_start, col_start, severity, message, code, context
        )

        for extra_message in extra_messages:
            match_code_loc = re.match(r".*:(\d+):(\d+):.*", extra_message)
            if not match_code_loc:
                continue
            line = int(match_code_loc.group(1)) - 1
            col = int(match_code_loc.group(2)) - 1
            extra_message = " ".join(extra_message.split(" ")[2:])
            context = self._code_context(
                rel_file, line, line, col, col + 1, extra_context=0
            )
            note = self._colorizer.note("Note")
            fancy_output += "\n" + self._diagnostic_message(
                rel_file, line, col, note, extra_message, "", context
            )

        return fancy_output

    def _make_file_output(
        self, file: pathlib.Path, diagnostic_outputs: Iterable[str]
    ) -> str:
        del file
        return "\n\n".join(diagnostic_outputs)

    def _make_whole_output(self, file_outputs: Iterable[str]) -> str:
        return "\n\n".join(file_outputs)
