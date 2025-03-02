#!/usr/bin/env python3

import argparse
import os
import signal
import subprocess
import sys
import threading
from typing import IO, Set, TextIO

from .diagnostic_formatter import (
    DiagnosticFormatter,
    CompactDiagnosticFormatter,
    FancyDiagnosticFormatter,
    GithubActionWorkflowCommandDiagnosticFormatter,
)
from .pylspclient.json_rpc_endpoint import JsonRpcEndpoint
from .pylspclient.lsp_endpoint import LspEndpoint
from .pylspclient.lsp_client import LspClient
from .pylspclient.lsp_structs import TextDocumentItem, LANGUAGE_IDENTIFIER
from .version import __version__

__all__ = ["main_cli"]


class ReadPipe(threading.Thread):
    def __init__(self, pipe: IO[bytes], out: TextIO):
        threading.Thread.__init__(self)
        self.pipe = pipe
        self.out = out

    def run(self):
        line = self.pipe.readline().decode("utf-8")
        while line:
            print(line, file=self.out)
            line = self.pipe.readline().decode("utf-8")


def kill_child_process(sig, _, child_processes, pbar):
    """Kill child processes on SIGINT"""
    assert sig == signal.SIGINT
    if pbar is not None:
        pbar.close()
    for child in child_processes:
        print(f"Terminating child process {child.pid}...", file=sys.stderr)
        child.terminate()
        child.wait()
        print(f"Child process {child.pid} terminated.", file=sys.stderr)
    sys.exit(1)


class FileExtensionFilter:
    def __init__(self, extensions: Set[str]):
        self.extensions = extensions

    def __call__(self, file_path):
        return os.path.splitext(file_path)[1][1:] in self.extensions


def _file_uri(path: str):
    return "file://" + path


def _uri_file(uri: str):
    if not uri.startswith("file://"):
        raise ValueError("Not a file URI: " + uri)
    return uri[7:]


def _is_output_supports_color(output: TextIO):
    return hasattr(output, "isatty") and output.isatty()


class DiagnosticCollector:
    SEVERITY_INT = {
        "error": 1,
        "warn": 2,
        "info": 3,
        "hint": 4,
    }

    def __init__(self):
        self.diagnostics = {}
        self.requested_files = set()
        self.cond = threading.Condition()

    def handle_publish_diagnostics(self, args):
        file = _uri_file(args["uri"])
        if file not in self.requested_files:
            return
        self.cond.acquire()
        self.diagnostics[file] = args["diagnostics"]
        self.cond.notify()
        self.cond.release()

    def request_diagnostics(self, lsp_client: LspClient, file_path: str):
        file_path = os.path.abspath(file_path)
        languageId = LANGUAGE_IDENTIFIER.CPP
        version = 1
        text = open(file_path, "r").read()
        self.requested_files.add(file_path)
        lsp_client.didOpen(
            TextDocumentItem(_file_uri(file_path), languageId, version, text)
        )

    def check_failed(self, fail_on_severity: str) -> bool:
        severity_level = self.SEVERITY_INT[fail_on_severity]
        for diagnostics in self.diagnostics.values():
            for diagnostic in diagnostics:
                if diagnostic["severity"] <= severity_level:
                    return True
        return False

    def format_diagnostics(self, formatter: DiagnosticFormatter) -> str:
        return formatter.format(sorted(self.diagnostics.items())).rstrip()


def main_cli():
    DEFAULT_ALLOW_EXTENSIONS = [
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
        help="Path to clangd executable. [default: clangd]",
    )
    parser.add_argument(
        "--allow-extensions",
        default=DEFAULT_ALLOW_EXTENSIONS,
        help=f"A comma-separated list of file extensions to allow. [default: {','.join(DEFAULT_ALLOW_EXTENSIONS)}]",
    )
    parser.add_argument(
        "--query-driver",
        default="",
        help="Comma separated list of globs for -listing gcc-compatible drivers that are safe to execute. Drivers matching any of these globs will be used to extract system includes. e.g. /usr/bin/**/clang-*,/path/to/repo/**/g++-*",
    )
    parser.add_argument(
        "--fail-on-severity",
        metavar="SEVERITY",
        choices=DiagnosticCollector.SEVERITY_INT.keys(),
        default="hint",
        help=f"On which severity of diagnostics this program should exit with a non-zero status. Candidates: {', '.join(DiagnosticCollector.SEVERITY_INT)}. [default: hint]",
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
        help="Files to check. Files whose extensions are not in ALLOW_EXTENSIONS will be ignored.",
    )
    args = parser.parse_args()

    ext_filter = FileExtensionFilter(set(map(str.strip, args.allow_extensions)))
    files = list(filter(ext_filter, args.filename))
    for file in files:
        if not os.path.isfile(file):
            print(f"File not found: {file}", file=sys.stderr)
            sys.exit(1)

    clangd_command = [
        f"{args.clangd_executable}",
        f"--compile-commands-dir={args.compile_commands_dir}",
        "--clang-tidy",
        f"--query-driver={args.query_driver}",
        f"-j={args.jobs}",
        "--pch-storage=memory",
        "--enable-config",
        "--offset-encoding=utf-16",
    ]

    p = subprocess.Popen(
        clangd_command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert p.stderr is not None
    read_pipe = ReadPipe(p.stderr, args.verbose and sys.stderr or open(os.devnull, "w"))
    read_pipe.start()

    # Kill clangd subprocess on SIGINT
    pbar = None  # use to close progress bar if it exists
    signal.signal(signal.SIGINT, lambda sig, _: kill_child_process(sig, _, [p], pbar))

    collector = DiagnosticCollector()

    json_rpc_endpoint = JsonRpcEndpoint(p.stdin, p.stdout)
    lsp_endpoint = LspEndpoint(
        json_rpc_endpoint,
        notify_callbacks={
            "textDocument/publishDiagnostics": lambda args: collector.handle_publish_diagnostics(
                args
            ),
        },
    )
    lsp_client = LspClient(lsp_endpoint)

    root_path = os.path.abspath(".")
    root_uri = _file_uri(root_path)
    workspace_folders = [{"name": "foo", "uri": root_uri}]

    lsp_client.initialize(p.pid, None, root_uri, None, None, "off", workspace_folders)
    lsp_client.initialized()

    for file in files:
        collector.request_diagnostics(lsp_client, file)

    if args.tqdm:
        try:
            from tqdm import tqdm
        except ImportError:
            print(
                "tqdm not found. Please install tqdm to enable progress bar.",
                file=sys.stderr,
            )
            args.tqdm = False

    if args.tqdm:
        from tqdm import tqdm

        with tqdm(total=len(files)) as pbar:
            collector.cond.acquire()
            while len(collector.diagnostics) < len(files):
                pbar.update(len(collector.diagnostics) - pbar.n)
                collector.cond.wait()
            pbar.update(len(collector.diagnostics) - pbar.n)
            collector.cond.release()
    else:
        collector.cond.acquire()
        while len(collector.diagnostics) < len(files):
            collector.cond.wait()
        collector.cond.release()

    lsp_client.shutdown()
    lsp_client.exit()
    lsp_endpoint.join()
    os.wait()
    if read_pipe.is_alive():
        read_pipe.join()

    formatter = (
        FancyDiagnosticFormatter(
            extra_context=args.context,
            enable_color=(
                _is_output_supports_color(args.output)
                if args.color == "auto"
                else args.color == "always"
            ),
        )
        if not args.compact
        else CompactDiagnosticFormatter()
    )
    print(collector.format_diagnostics(formatter), file=args.output)
    if args.github:
        print(
            collector.format_diagnostics(
                GithubActionWorkflowCommandDiagnosticFormatter(args.git_root)
            ),
            file=args.output,
        )
    if collector.check_failed(args.fail_on_severity):
        exit(1)
