import argparse
import json
import os
import pathlib
import sys

import cattrs

from .line_filter import LineFilter
from .lsp.messages import DiagnosticSeverity
from .version import __version__

__all__ = ["SEVERITY_INT", "parse_args"]


SEVERITY_INT = dict(
    error=DiagnosticSeverity.ERROR,
    warn=DiagnosticSeverity.WARNING,
    info=DiagnosticSeverity.INFORMATION,
    hint=DiagnosticSeverity.HINT,
)


def parse_args() -> argparse.Namespace:
    DEFAULT_ALLOWED_EXTENSIONS = [
        "c",
        "h",
        "cpp",
        "cc",
        "cxx",
        "hpp",
        "hh",
        "hxx",
        "cu",
        "cuh",
    ]

    parser = argparse.ArgumentParser(
        prog="clangd-tidy",
        description="Run clangd with clang-tidy and output diagnostics. This aims to serve as a faster alternative to clang-tidy.",
        epilog="Find more information on https://github.com/lljbash/clangd-tidy.",
        add_help=False,
    )

    input_group = parser.add_argument_group("input options")
    input_group.add_argument(
        "filename",
        nargs="+",
        type=pathlib.Path,
        help="Files to analyze. Ignores files with extensions not listed in ALLOW_EXTENSIONS.",
    )
    input_group.add_argument(
        "--allow-extensions",
        type=lambda x: x.strip().split(","),
        default=DEFAULT_ALLOWED_EXTENSIONS,
        help=f"A comma-separated list of file extensions to allow. [default: {','.join(DEFAULT_ALLOWED_EXTENSIONS)}]",
    )

    check_group = parser.add_argument_group("check options")
    check_group.add_argument(
        "--fail-on-severity",
        metavar="SEVERITY",
        choices=SEVERITY_INT.keys(),
        default="hint",
        help=f"Specifies the diagnostic severity level at which the program exits with a non-zero status. Possible values: {', '.join(SEVERITY_INT.keys())}. [default: hint]",
    )
    check_group.add_argument(
        "-f",
        "--format",
        action="store_true",
        help="Also check code formatting with clang-format. Exits with a non-zero status if any file violates formatting rules.",
    )

    output_group = parser.add_argument_group("output options")
    output_group.add_argument(
        "-o",
        "--output",
        type=argparse.FileType("w"),
        default=sys.stdout,
        help="Output file for diagnostics. [default: stdout]",
    )
    output_group.add_argument(
        "--line-filter",
        type=lambda x: cattrs.structure(json.loads(x), LineFilter),
        help=(
            "A JSON with a list of files and line ranges that will act as a filter for diagnostics."
            " Compatible with clang-tidy --line-filter parameter format."
        ),
    )
    output_group.add_argument(
        "--tqdm", action="store_true", help="Show a progress bar (tqdm required)."
    )
    output_group.add_argument(
        "--github",
        action="store_true",
        help="Append workflow commands for GitHub Actions to output.",
    )
    output_group.add_argument(
        "--git-root",
        default=os.getcwd(),
        help="Specifies the root directory of the Git repository. Only works with --github. [default: current directory]",
    )
    output_group.add_argument(
        "-c",
        "--compact",
        action="store_true",
        help="Print compact diagnostics (legacy).",
    )
    output_group.add_argument(
        "--context",
        type=int,
        default=2,
        help="Number of additional lines to display on both sides of each diagnostic. This option is ineffective with --compact. [default: 2]",
    )
    output_group.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="Colorize the output. This option is ineffective with --compact. [default: auto]",
    )
    output_group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Stream verbose output from clangd to stderr.",
    )

    clangd_group = parser.add_argument_group("clangd options")
    clangd_group.add_argument(
        "-p",
        "--compile-commands-dir",
        default="build",
        help="Specify a path to look for compile_commands.json. If the path is invalid, clangd will look in the current directory and parent paths of each source file. [default: build]",
    )
    clangd_group.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        help="Number of async workers used by clangd. Background index also uses this many workers. [default: 1]",
    )
    clangd_group.add_argument(
        "--clangd-executable",
        default="clangd",
        help="Clangd executable. [default: clangd]",
    )
    clangd_group.add_argument(
        "--query-driver",
        default="",
        help="Comma separated list of globs for white-listing gcc-compatible drivers that are safe to execute. Drivers matching any of these globs will be used to extract system includes. e.g. `/usr/bin/**/clang-*,/path/to/repo/**/g++-*`.",
    )

    misc_group = parser.add_argument_group("generic options")
    misc_group.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program's version number and exit.",
    )
    misc_group.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit."
    )

    return parser.parse_args()
