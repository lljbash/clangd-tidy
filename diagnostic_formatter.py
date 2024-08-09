from abc import ABC, abstractmethod
import json
import os
import re
import hashlib
import pathlib
from typing import Any, Iterable, Optional, Tuple


DiagnosticCollection = Iterable[Tuple[str, Any]]


class DiagnosticFormatter(ABC):
    SEVERITY = {
        1: "Error",
        2: "Warning",
        3: "Information",
        4: "Hint",
    }

    @abstractmethod
    def format(self, diagnostic_collection: DiagnosticCollection) -> str:
        pass


class CompactDiagnosticFormatter(DiagnosticFormatter):
    def format(self, diagnostic_collection: DiagnosticCollection) -> str:
        output = ""
        for file, diagnostics in diagnostic_collection:
            if len(diagnostics) == 0:
                continue
            output += "----- {} -----\n\n".format(os.path.relpath(file))
            for diagnostic in diagnostics:
                source = diagnostic.get("source", None)
                severity = diagnostic.get("severity", None)
                code = diagnostic.get("code", None)
                extra_info = "{}{}{}".format(
                    f" {source}" if source else "",
                    f" {self.SEVERITY[severity]}" if severity else "",
                    f" [{code}]" if code else "",
                )
                line = diagnostic["range"]["start"]["line"] + 1
                col = diagnostic["range"]["start"]["character"] + 1
                message = diagnostic["message"]
                if source is None and code is None:
                    continue
                output += f"- line {line}, col {col}:{extra_info}\n{message}\n\n"
            output += "\n"
        return output


class GithubActionWorkflowCommandDiagnosticFormatter(DiagnosticFormatter):
    SEVERITY_GITHUB = {
        1: "error",
        2: "warning",
        3: "notice",
        4: "notice",
    }

    def __init__(self, git_root: str):
        self._git_root = git_root

    def format(self, diagnostic_collection: DiagnosticCollection) -> str:
        commands = "::group::{workflow commands}\n"
        for file, diagnostics in diagnostic_collection:
            if len(diagnostics) == 0:
                continue
            for diagnostic in diagnostics:
                source = diagnostic.get("source", None)
                severity = diagnostic.get("severity", None)
                code = diagnostic.get("code", None)
                extra_info = "{}{}{}".format(
                    f"{source}" if source else "",
                    f" {self.SEVERITY[severity]}" if severity else "",
                    f" [{code}]" if code else "",
                )
                line = diagnostic["range"]["start"]["line"] + 1
                end_line = diagnostic["range"]["end"]["line"] + 1
                col = diagnostic["range"]["start"]["character"] + 1
                end_col = diagnostic["range"]["end"]["character"] + 1
                message = diagnostic["message"]
                if source is None and code is None:
                    continue
                command = self.SEVERITY_GITHUB[severity]
                rel_file = os.path.relpath(file, self._git_root)
                commands += f"::{command} file={rel_file},line={line},endLine={end_line},col={col},endCol={end_col},title={extra_info}::{message}\n"
        commands += "::endgroup::"
        return commands


class GitLabCodeQualityDiagnosticFormatter(DiagnosticFormatter):
    """
    Format diagnostics as GitLab Code Quality report.
    See https://docs.gitlab.com/ee/ci/testing/code_quality.html#implement-a-custom-tool
    """
    SEVERITY_GITLAB = {
        1: "critical",
        2: "major",
        3: "minor",
        4: "info",
    }

    def __init__(self, output_file: str, git_root: str):
        self._output_file = output_file
        self._git_root = git_root
        self._whitespace_pattern = re.compile(r'\s+')

    def create_fingerprint(self, source, file, message, code):
        # First combine filename, message and code, remove all whitespace and create a hash.
        # Then, prefix with the stripped source and return.
        filename = pathlib.Path(file).name
        hash_str = re.sub(self._whitespace_pattern, '',
                          f"{filename}:{message}:{code}")
        # Create SHA256 hash digest
        hash_digest = hashlib.sha256(hash_str.encode()).hexdigest()
        # Clean up source
        source = re.sub(self._whitespace_pattern, '', source)
        return f"{source}:{hash_digest}"

    def write_to_file(self, items):
        with open(self._output_file, "w") as f:
            f.write(json.dumps(items))

    def format(self, diagnostic_collection: DiagnosticCollection) -> str:
        fix_available_suffix = " (fix available)"
        items = []
        for file, diagnostics in diagnostic_collection:
            if len(diagnostics) == 0:
                continue
            for diagnostic in diagnostics:
                code = diagnostic.get("code", None)
                if code is None:
                    continue
                source = diagnostic.get("source", "clangd-tidy")
                severity = diagnostic.get("severity", 4)
                line = diagnostic["range"]["start"]["line"] + 1
                end_line = diagnostic["range"]["end"]["line"] + 1
                message = diagnostic["message"].strip()
                # If message ends with " (fix available)", remove it.
                if message.endswith(fix_available_suffix):
                    message = message[:-len(fix_available_suffix)].strip()
                rel_file = os.path.relpath(file, self._git_root)
                entry = {}
                entry["fingerprint"] = self.create_fingerprint(
                    source, rel_file, message, code)
                entry["description"] = f"{message}"
                entry["severity"] = self.SEVERITY_GITLAB[severity]
                entry["location"] = {
                    "path": rel_file,
                    "lines": {
                        "begin": line,
                        "end": end_line
                    }
                }
                items.append(entry)
        # Write JSON to _output_file
        self.write_to_file(items)
        return ""


class FancyDiagnosticFormatter(DiagnosticFormatter):
    class Colorizer:
        class ColorSeqTty:
            ERROR = "\033[91m"
            WARNING = "\033[93m"
            INFO = "\033[96m"
            HINT = "\033[94m"
            NOTE = "\033[90m"
            GREEN = "\033[92m"
            BOLD = "\033[1m"
            ENDC = "\033[0m"

        class ColorSeqNoTty:
            ERROR = ""
            WARNING = ""
            INFO = ""
            HINT = ""
            NOTE = ""
            GREEN = ""
            BOLD = ""
            ENDC = ""

        def __init__(self, enable_color: bool):
            self.color_seq = self.ColorSeqTty if enable_color else self.ColorSeqNoTty

        def per_severity(self, severity: int, message: str):
            if severity == 1:
                return f"{self.color_seq.ERROR}{message}{self.color_seq.ENDC}"
            if severity == 2:
                return f"{self.color_seq.WARNING}{message}{self.color_seq.ENDC}"
            if severity == 3:
                return f"{self.color_seq.INFO}{message}{self.color_seq.ENDC}"
            if severity == 4:
                return f"{self.color_seq.HINT}{message}{self.color_seq.ENDC}"
            return message

        def highlight(self, message: str):
            return f"{self.color_seq.GREEN}{message}{self.color_seq.ENDC}"

        def note(self, message: str):
            return f"{self.color_seq.NOTE}{message}{self.color_seq.ENDC}"

    def __init__(self, extra_context: int, enable_color: bool):
        self._extra_context = extra_context
        self._colorizer = self.Colorizer(enable_color)

    def _colorized_severity(self, severity: int):
        return self._colorizer.per_severity(severity, self.SEVERITY[severity])

    @staticmethod
    def _prepend_line_number(line: str, lino: Optional[int]) -> str:
        LINO_WIDTH = 5
        LINO_SEP = " |  "
        lino_str = str(lino) if lino else ""
        return f"{lino_str :{LINO_WIDTH}}{LINO_SEP}{line.rstrip()}\n"

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

        return context

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

    def format(self, diagnostic_collection: DiagnosticCollection) -> str:
        fancy_output = ""

        for file, diagnostics in diagnostic_collection:
            if len(diagnostics) == 0:
                continue
            file = os.path.relpath(file)
            for diagnostic in diagnostics:
                message: str = diagnostic["message"].replace(" (fix available)", "")
                message_list = [line for line in message.splitlines() if line.strip()]
                message, extra_messages = message_list[0], message_list[1:]

                raw_code = diagnostic.get("code", None)
                if not raw_code:
                    continue
                code = f"[{raw_code}]" if raw_code else ""

                raw_severity = diagnostic.get("severity", None)
                severity = (
                    self._colorized_severity(raw_severity) if raw_severity else ""
                )

                line_start = diagnostic["range"]["start"]["line"]
                line_end = diagnostic["range"]["end"]["line"]

                col_start = diagnostic["range"]["start"]["character"]
                col_end = diagnostic["range"]["end"]["character"]

                context = self._code_context(
                    file, line_start, line_end, col_start, col_end
                )

                fancy_output += self._diagnostic_message(
                    file, line_start, col_start, severity, message, code, context
                )

                for extra_message in extra_messages:
                    match_code_loc = re.match(r".*:(\d+):(\d+):.*", extra_message)
                    if not match_code_loc:
                        continue
                    line = int(match_code_loc.group(1)) - 1
                    col = int(match_code_loc.group(2)) - 1
                    extra_message = " ".join(extra_message.split(" ")[2:])
                    context = self._code_context(
                        file, line, line, col, col + 1, extra_context=0
                    )
                    note = self._colorizer.note("Note")
                    fancy_output += self._diagnostic_message(
                        file, line, col, note, extra_message, "", context
                    )

                fancy_output += "\n"

        return fancy_output
