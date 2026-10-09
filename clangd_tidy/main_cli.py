#!/usr/bin/env python3

import argparse
import asyncio
import logging
import pathlib
import sys
import tempfile
from typing import (
    Any,
    Collection,
    Dict,
    List,
    Optional,
    TextIO,
    Tuple,
    Type,
)
from types import TracebackType
from unittest.mock import MagicMock


import cattrs
import yaml

from clangd_tidy.flows.base import BaseFlow

from .flows.diagnostics import DiagnosticsFlow
from .flows.formatting import FormattingFlow
from .args import SEVERITY_INT, parse_args
from .replacements import (
    create_replacements,
)
from .flows.fix import DiagnosticsWithFixesFlow
from .diagnostic_formatter import (
    CompactDiagnosticFormatter,
    DiagnosticCollection,
    FancyDiagnosticFormatter,
    GithubActionWorkflowCommandDiagnosticFormatter,
)
from .lsp import ClangdAsync
from .lsp.messages import (
    ClientCapabilities,
    Diagnostic,
    uri_to_path,
    WindowClientCapabilities,
    WorkspaceEdit,
)
from .progress import IndexingProgressTracker
from .event_bus import EventBus


__all__ = ["main_cli"]

# Cattrs converter
converter = cattrs.Converter()


def _is_output_supports_color(output: TextIO) -> bool:
    return hasattr(output, "isatty") and output.isatty()


def _try_import_tqdm(enabled: bool):
    if enabled:
        try:
            from tqdm import tqdm  # type: ignore

            return tqdm
        except ImportError:
            print(
                "tqdm is not installed. The progress bar feature is disabled.",
                file=sys.stderr,
            )
    return MagicMock()


class ClangdRecoverableError(Exception):
    """Indicates a recoverable error in clangd operation, such as a crash."""

    pass


class ClangdRunner:
    """
    Orchestrates the overall analysis process, managing the `ClangdAsync`
    client and handling the high-level logic of fetching diagnostics, applying
    fixes, and reporting results.

    This class is responsible for:
    - Managing the lifecycle of the `ClangdAsync` client.
    - Iterating over the files to be analyzed.
    - Collecting all diagnostics and edits.
    - Handling crash recovery at the application level.
    - Applying fixes via `clang-apply-replacements` or exporting them.
    - Formatting and printing diagnostics to the console.
    """

    def __init__(
        self,
        clangd: ClangdAsync,
        files: Collection[pathlib.Path],
        run_format: bool,
        clang_apply_replacements_executable: str,
        run_fix: bool,
        format_style: str,
        tqdm: bool,
        event_bus: EventBus,
    ):
        self._clangd = clangd
        self._files = files
        self._run_format = run_format
        self._clang_apply_replacements_executable = clang_apply_replacements_executable
        self._run_fix = run_fix
        self._format_style = format_style
        self._tqdm = tqdm
        self._system_tasks: dict[Any, asyncio.Task[None]] = (
            {}
        )  # Tasks for internal systems
        self._tasks: set[asyncio.Task[Any]] = set()  # Tasks spawned by flows etc.
        self._event_bus = event_bus

        self._pbar = _try_import_tqdm(self._tqdm)(
            total=len(self._files) * (2 if self._run_format else 1)
            + (1 if self._run_fix else 0),
            desc="Analyzing files",
            unit="file",
        )

        self._flows: List[BaseFlow] = []

        self._indexing_progress_tracker = IndexingProgressTracker(
            self._clangd, self._event_bus
        )
        self._flows.append(self._indexing_progress_tracker)

        self._diagnostics_flow = self._create_diagnostics_flow()
        self._flows.append(self._diagnostics_flow)

        self._formatting_flow: Optional[FormattingFlow] = None
        if self._run_format:
            self._formatting_flow = FormattingFlow(
                self._clangd,
                self._event_bus,
                self._files,
            )
            self._flows.append(self._formatting_flow)

    def _create_diagnostics_flow(self) -> DiagnosticsFlow:
        if self._run_fix:
            return DiagnosticsWithFixesFlow(
                self._clangd,
                self._event_bus,
                self._files,
                self._indexing_progress_tracker,
            )
        return DiagnosticsFlow(
            self._clangd,
            self._event_bus,
            self._files,
        )

    def _required_capabilities(self) -> ClientCapabilities:
        client_capabilities = ClientCapabilities(
            window=WindowClientCapabilities(workDoneProgress=True),
            offsetEncoding=["utf-8"],
            positionEncodings=["utf-8"],
        )

        for flow in self._flows:
            client_capabilities = flow.add_required_capabilities(client_capabilities)
        return client_capabilities

    async def _bootstrap(self):
        """
        Bootstraps the runner after a start/clangd restart.
        """
        for flow in self._flows:
            await flow.bootstrap()

        await self._clangd.start()
        if not "event_bus" in self._system_tasks:
            # Event bus persists across restarts, so only start it once
            self._system_tasks["event_bus"] = asyncio.create_task(self._event_bus.run())

        # Start accepting and dispatching messages from clangd
        self._system_tasks["message_processor"] = asyncio.create_task(
            self._message_processor_task()
        )

        init_resp = await self._clangd.initialize(
            pathlib.Path.cwd(), self._required_capabilities()
        )
        assert init_resp.error is None, "Initialization failed"

        result: Dict[str, Any] = init_resp.result or {}
        for flow in self._flows:
            flow.check_clangd_capabilities(result)

        await self._clangd.initialized()

        for flow in self._flows:
            await flow.run()

    async def _cleanup(self) -> None:
        """Cleans up the runner by cancelling system tasks."""
        for system_task in self._system_tasks.values():
            system_task.cancel()
        cleanup_results = await asyncio.gather(
            *self._system_tasks.values(), return_exceptions=True
        )
        for result in cleanup_results:
            if isinstance(result, Exception):
                logging.exception(
                    f"System task raised an exception during cleanup: {result}",
                    exc_info=result,
                )
        self._system_tasks.clear()

    async def __aenter__(self):
        try:
            await self._bootstrap()
            return self
        except Exception:
            # Stop everything on exception
            await self._cleanup()
            raise

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ):
        await self._cleanup()
        for flow in self._flows:
            await flow.shutdown()
        await self.shutdown()

    async def shutdown(self):
        """Shutdown the clangd process."""
        logging.debug("Shutting down clangd...")
        try:
            await self._clangd.shutdown()
            await self._clangd.exit()
            logging.debug("Clangd shutdown complete")
        except (ConnectionError, EOFError) as e:
            # Already dead, that's fine
            logging.debug(f"Clangd already terminated during shutdown: {e}")
            pass

    async def export_fixes(
        self,
        edits: List[Tuple[Diagnostic, WorkspaceEdit]],
        output_path: pathlib.Path,
    ) -> None:
        """Export the given edits to a YAML file."""
        all_replacements = create_replacements(edits)
        if not all_replacements:
            return

        main_source_file = ""
        if edits:
            # Find the first edit with changes to determine the main source file
            for _, edit in edits:
                if edit.changes:
                    first_uri = next(iter(edit.changes.keys()), None)
                    if first_uri:
                        main_source_file = str(uri_to_path(first_uri))
                        break

        with open(output_path, "w") as f:
            logging.debug(f"Exporting fixes to {output_path}")
            fixes = yaml.dump(
                {
                    "MainSourceFile": main_source_file,
                    "Diagnostics": converter.unstructure(all_replacements),
                },
            )
            logging.debug(f"Fixes: \n{fixes}")
            f.write(fixes)

    async def apply_fixes(self, edits: List[Tuple[Diagnostic, WorkspaceEdit]]) -> None:
        """Apply the given edits to the files."""
        all_replacements = create_replacements(edits)
        if not all_replacements:
            return

        # Create a temporary directory to store the replacements file
        with tempfile.TemporaryDirectory() as tmpdir:
            await self.export_fixes(edits, pathlib.Path(tmpdir) / "fixes.yaml")

            # Build command arguments
            cmd_args = [self._clang_apply_replacements_executable]

            # Add formatting flags if format_style is specified and not 'none'
            if self._format_style and self._format_style.lower() != "none":
                cmd_args.append("--format")
                cmd_args.append(f"--style={self._format_style}")

            cmd_args.append(str(tmpdir))  # Pass the directory containing the YAML file

            logging.debug(f"Running: {' '.join(cmd_args)}")

            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = f"clang-apply-replacements failed with return code {process.returncode}"
                if stderr:
                    error_msg += f": {stderr.decode().strip()}"
                logging.error(error_msg)
                print(f"Error: {error_msg}", file=sys.stderr)
                raise RuntimeError(error_msg)

    async def _message_processor_task(
        self,
    ) -> None:
        """Background task that continuously processes messages from clangd."""
        try:
            while True:
                await self._clangd.recv_and_publish()
        except (ConnectionError, EOFError) as e:
            logging.error(f"Clangd crashed in message processor: {e}")
            raise ClangdRecoverableError from e
        except Exception as e:
            logging.error(f"Unexpected error in message processor: {e}", exc_info=True)
            raise

    async def collect_analysis(
        self,
    ) -> Tuple[DiagnosticCollection, List[Tuple[Diagnostic, WorkspaceEdit]]]:

        try:
            while True:
                flow_futures: set[asyncio.Future[Any]] = set()
                for flow in self._flows:
                    flow_futures.update(flow.pending_futures())

                if not flow_futures:
                    # If everything is done, exit the loop.
                    break

                await asyncio.wait(
                    [*self._system_tasks.values(), *flow_futures],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                done_system_tasks = [
                    system_task
                    for system_task in self._system_tasks.values()
                    if system_task.done()
                ]
                recovering = False
                for system_task in done_system_tasks:
                    try:
                        system_task.result()
                    except ClangdRecoverableError:
                        logging.info("Recovering from clangd crash...")
                        try:
                            recovering |= await self._recover_from_clangd_crash()
                        except Exception as e:
                            logging.error(
                                f"Failed to recover from clangd crash: {e}",
                                exc_info=e,
                            )
                            raise
                    except Exception as e:
                        logging.exception(
                            f"System task {system_task} failed: {e}", exc_info=e
                        )
                        break  # If we had any non-recoverable error, stop
                else:
                    # We didn't break, meaning no non-recoverable error occurred
                    continue
                if done_system_tasks and not recovering:
                    # Any system task finished cleanly - we should exit the loop
                    break

        except KeyboardInterrupt:
            logging.warning("Analysis cancelled by user (Ctrl+C)")

            raise
        except Exception as e:
            logging.error(f"Unexpected error during analysis: {e}", exc_info=True)
            raise
        finally:
            self._pbar.close()

        edits: List[Tuple[Diagnostic, WorkspaceEdit]] = (
            self._diagnostics_flow.get_edits()
            if isinstance(self._diagnostics_flow, DiagnosticsWithFixesFlow)
            else []
        )
        diagnostics: DiagnosticCollection = {}
        if self._diagnostics_flow:
            diagnostics.update(self._diagnostics_flow.get_diagnostics())
        if self._formatting_flow:
            diagnostics.update(self._formatting_flow.get_formatting_diagnostics())
        return diagnostics, edits

    async def _recover_from_clangd_crash(self) -> bool:
        """Handles recovery from a clangd crash.

        Returns:
            True if recovery was attempted, False if not."""
        if any(not flow.completed() for flow in self._flows):
            logging.warning(f"Clangd crashed with pending work - attempting recovery")
            await self.shutdown()  # Ensure we do a proper cleanup
            await self._bootstrap()  # Restart everything. This doesn't touch state that
            # needs to be preserved, such as failed fixes.
            return True
        else:
            logging.info("Clangd crashed but no pending work - exiting")
            return False


def _print_diagnostics(
    file_diagnostics: DiagnosticCollection, args: argparse.Namespace
) -> None:
    """Print diagnostics to output.

    Args:
        file_diagnostics: Dictionary mapping file paths to lists of diagnostics
        args: Parsed command-line arguments
    """
    # Apply line filter if specified
    if args.line_filter is not None:
        file_diagnostics = {
            file: [
                diagnostic
                for diagnostic in diagnostics
                if args.line_filter.passes_line_filter(file, diagnostic)
            ]
            for file, diagnostics in file_diagnostics.items()
        }

    # Format diagnostics
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

    # Print formatted diagnostics
    formatted_diagnostics = formatter.format(file_diagnostics)
    print(formatted_diagnostics, file=args.output)

    # Print GitHub Actions format if requested
    if args.github:
        github_formatted_diagnostics = GithubActionWorkflowCommandDiagnosticFormatter(
            args.git_root
        ).format(file_diagnostics)
        print(github_formatted_diagnostics, file=args.output)


def main_cli() -> int:
    """The main entry point for the CLI."""
    return asyncio.run(main_cli_async())


async def main_cli_async() -> int:
    """The main entry point for the CLI."""
    args = parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s:%(name)s:%(message)s",
        stream=sys.stderr,
    )

    files: List[pathlib.Path] = args.filename
    files = [
        file.resolve() for file in files if file.suffix[1:] in args.allow_extensions
    ]
    missing_files = [str(file) for file in files if not file.is_file()]
    if missing_files:
        logging.error(f"File(s) not found: {', '.join(missing_files)}")
        print(f"File(s) not found: {', '.join(missing_files)}", file=sys.stderr)
        return 1

    # Create EventBus instance
    event_bus = EventBus()

    # Create clangd instance
    clangd = ClangdAsync(
        clangd_executable=args.clangd_executable,
        event_bus=event_bus,
        compile_commands_dir=args.compile_commands_dir,
        jobs=args.jobs,
        verbose=args.verbose,
        query_driver=args.query_driver,
        use_background_index=args.fix or bool(args.export_fixes),
    )

    runner = ClangdRunner(
        clangd=clangd,
        files=files,
        run_format=args.format,
        clang_apply_replacements_executable=args.clang_apply_replacements_executable,
        run_fix=args.fix or bool(args.export_fixes),
        format_style=args.format_style,
        tqdm=args.tqdm,
        event_bus=event_bus,
    )

    return_code = 0
    try:
        async with runner:
            file_diagnostics, edits = await runner.collect_analysis()
            # Always print diagnostics (unless in quiet mode or similar)
            _print_diagnostics(file_diagnostics, args)

            # Handle export-fixes mode
            if args.export_fixes:
                if edits:
                    await runner.export_fixes(edits, args.export_fixes)
                    print(f"Exported fixes to {args.export_fixes}", file=sys.stderr)
                else:
                    # Create an empty file
                    with open(args.export_fixes, "w"):
                        pass
                    print(
                        f"No fixes to export, created empty file at {args.export_fixes}",
                        file=sys.stderr,
                    )
                return 0

            # Handle fix mode
            if args.fix:
                if edits:
                    await runner.apply_fixes(edits)
                else:
                    logging.debug("No fixes to apply.")
                return 0

            # No action mode - just check severity for exit code
            if any(
                any(
                    (
                        diagnostic.severity
                        and diagnostic.severity <= SEVERITY_INT[args.fail_on_severity]
                    )
                    or diagnostic.source == "clang-format"
                    for diagnostic in diagnostics
                )
                for diagnostics in file_diagnostics.values()
            ):
                return_code = 1
    except Exception as e:
        logging.error(
            f"An unexpected error occurred during analysis: {e}", exc_info=True
        )
        return_code = 1

    return return_code
