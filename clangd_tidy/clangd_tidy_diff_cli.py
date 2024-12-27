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

from .version import __version__

ADDED_FILE_NAME_REGEX = re.compile(r'^\+\+\+ "?(?P<prefix>.*?/)(?P<file>[^\s"]*)')
ADDED_LINES_REGEX = re.compile(r"^@@.*\+(?P<line>\d+)(,(?P<count>\d+))?")


def clang_tidy_diff():
    parser = argparse.ArgumentParser(
        description="Runs clangd-tidy against modified files,"
        " and returns diagnostics only on changed lines."
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

    last_file = None
    lines_per_file = {}
    for line in sys.stdin:
        m = re.search(ADDED_FILE_NAME_REGEX, line)
        if m:
            last_file = m.group("file")

        if last_file is None:
            continue

        m = re.search(ADDED_LINES_REGEX, line)
        if m is None:
            continue

        start_line = int(m.group("line"))
        line_count = 1
        if m.group("count") is not None:
            line_count = int(m.group("count"))
        if line_count == 0:
            continue

        end_line = start_line + line_count - 1
        lines_per_file.setdefault(last_file, []).append([start_line, end_line])

    if len(lines_per_file) == 0:
        print("No relevant changes found.")
        sys.exit(0)

    filters = []
    for file, lines in lines_per_file.items():
        filters.append({"name": file, "lines": lines})

    filters_json = json.dumps(filters)
    command = ["clangd-tidy", "--line-filter", filters_json]

    if args.compile_commands_dir:
        command.extend(["--compile-commands-dir", args.compile_commands_dir])

    if args.pass_arg:
        command.extend(args.pass_arg)

    files = list(lines_per_file.keys())
    command.append("--")
    command.extend(files)

    sys.exit(subprocess.run(command).returncode)


if __name__ == "__main__":
    clang_tidy_diff()
