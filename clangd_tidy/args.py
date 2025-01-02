import argparse
import pathlib
import sys
import os

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
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "-p",
        "--compile-commands-dir",
        default="build",
        help="Specify a path to look for compile_commands.json. If the path is invalid, clangd will look in the current directory and parent paths of each source file. [default: build]",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        help="Number of async workers used by clangd. Background index also uses this many workers. [default: 1]",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=argparse.FileType("w"),
        default=sys.stdout,
        help="Output file for diagnostics. [default: stdout]",
    )
    parser.add_argument(
        "--clangd-executable",
        default="clangd",
        help="Clangd executable. [default: clangd]",
    )
    parser.add_argument(
        "--allow-extensions",
        type=lambda x: x.strip().split(","),
        default=DEFAULT_ALLOWED_EXTENSIONS,
        help=f"A comma-separated list of file extensions to allow. [default: {','.join(DEFAULT_ALLOWED_EXTENSIONS)}]",
    )
    parser.add_argument(
        "--fail-on-severity",
        metavar="SEVERITY",
        choices=SEVERITY_INT.keys(),
        default="hint",
        help=f"On which severity of diagnostics this program should exit with a non-zero status. Candidates: {', '.join(SEVERITY_INT.keys())}. [default: hint]",
    )
    parser.add_argument(
        "--tqdm", action="store_true", help="Show a progress bar (tqdm required)."
    )
    parser.add_argument(
        "--github",
        action="store_true",
        help="Append workflow commands for GitHub Actions to output.",
    )
    parser.add_argument(
        "--git-root",
        default=os.getcwd(),
        help="Root directory of the git repository. Only works with --github. [default: current directory]",
    )
    parser.add_argument(
        "-c",
        "--compact",
        action="store_true",
        help="Print compact diagnostics (legacy).",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=2,
        help="Number of additional lines to display on both sides of each diagnostic. This option is ineffective with --compact. [default: 2]",
    )
    parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="Colorize the output. This option is ineffective with --compact. [default: auto]",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show verbose output from clangd."
    )
    parser.add_argument(
        "filename",
        nargs="+",
        type=pathlib.Path,
        help="Files to check. Files whose extensions are not in ALLOW_EXTENSIONS will be ignored.",
    )
    return parser.parse_args()
