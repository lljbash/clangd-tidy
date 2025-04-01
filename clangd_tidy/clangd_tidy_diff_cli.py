"""
Receives a diff on stdin and runs clangd-tidy only on the changed lines.
This is useful to slowly onboard a codebase to linting or to find regressions.
Inspired by clang-tidy-diff.py from the LLVM project.

Example usage with git:
    git diff -U0 HEAD^^..HEAD | clangd-tidy-diff -p my/build
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, NoReturn, Optional, TextIO, Union

import cattrs

from .line_filter import FileLineFilter, LineFilter, LineRange
from .version import __version__


def _parse_gitdiff(
    text: TextIO, add_file_range_callback: Callable[[Path, int, int], None]
) -> None:
    """
    Parses a git diff and calls add_file_range_callback for each added line range.
    """
    ADDED_FILE_NAME_REGEX = re.compile(r'^\+\+\+ "?(?P<prefix>.*?/)(?P<file>[^\s"]*)')
    ADDED_LINES_REGEX = re.compile(r"^@@.*\+(?P<line>\d+)(,(?P<count>\d+))?")

    last_file: Optional[str] = None
    for line in text:
        m = re.search(ADDED_FILE_NAME_REGEX, line)
        if m is not None:
            last_file = m.group("file")
        if last_file is None:
            continue

        m = re.search(ADDED_LINES_REGEX, line)
        if m is None:
            continue
        start_line = int(m.group("line"))
        line_count = int(m.group("count")) if m.group("count") else 1
        if line_count == 0:
            continue
        end_line = start_line + line_count - 1

        add_file_range_callback(Path(last_file), start_line, end_line)


def clang_tidy_diff() -> NoReturn:
    parser = argparse.ArgumentParser(
        description="Run clangd-tidy on modified files, reporting diagnostics only for changed lines.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "-p",
        "--compile-commands-dir",
        help="Specify a path to look for compile_commands.json. If the path is invalid, clangd-tidy will look in the current directory and parent paths of each source file.",
    )
    parser.add_argument(
        "--pass-arg",
        action="append",
        help="Pass this argument to clangd-tidy (can be used multiple times)",
    )
    args = parser.parse_args()

    line_filter_map: Dict[Path, FileLineFilter] = {}
    _parse_gitdiff(
        sys.stdin,
        lambda file, start, end: line_filter_map.setdefault(
            file.resolve(), FileLineFilter(file.resolve(), [])
        ).lines.append(LineRange(start, end)),
    )
    if not line_filter_map:
        print("No relevant changes found.", file=sys.stderr)
        sys.exit(0)

    line_filter = LineFilter(list(line_filter_map.values()))
    filters_json = json.dumps(cattrs.unstructure(line_filter))
    command: List[Union[str, bytes, Path]] = [
        "clangd-tidy",
        "--line-filter",
        filters_json,
    ]

    if args.compile_commands_dir:
        command.extend(["--compile-commands-dir", args.compile_commands_dir])

    if args.pass_arg:
        command.extend(args.pass_arg)

    files = line_filter_map.keys()
    command.append("--")
    command.extend(files)

    sys.exit(subprocess.run(command).returncode)


if __name__ == "__main__":
    clang_tidy_diff()
