"""
Shared test utilities for clangd-tidy test suite.

This module provides common functionality for testing clangd-tidy features:
- Running clangd-tidy with proper environment setup
- Managing test fixtures
- Assertion helpers
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import List, Optional


def run_clangd_tidy(
    args: List[str],
    cwd: Optional[pathlib.Path] = None,
    capture_output: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run clangd-tidy with proper environment setup.

    Args:
        args: Command-line arguments (excluding 'python3 -m clangd_tidy')
        cwd: Working directory (default: None, uses current directory)
        capture_output: Whether to capture stdout/stderr

    Returns:
        CompletedProcess object with stdout, stderr, and returncode

    Raises:
        AssertionError: If clangd_tidy exits with non-zero status
    """
    command = [sys.executable, "-m", "clangd_tidy"] + args
    env = os.environ.copy()
    project_root = pathlib.Path(__file__).parent.parent
    # Ensure we use the current project root for imports to avoid running tests against another installed version
    env["PYTHONPATH"] = str(project_root) + os.pathsep + env.get("PYTHONPATH", "")

    process = subprocess.run(
        command, capture_output=capture_output, text=True, env=env, cwd=cwd, check=check
    )
    print(process.stdout)
    print(process.stderr, file=sys.stderr)
    return process


def copy_fixture_directory(
    fixture_name: str, tmp_path: pathlib.Path, make_paths_absolute: bool = False
) -> pathlib.Path:
    """Copy a test fixture directory to a temporary directory and optionally update paths.

    Args:
        fixture_name: Name of the fixture directory in test/fixtures/
        tmp_path: Temporary directory provided by pytest
        make_paths_absolute: If True, convert relative paths in compile_commands.json to absolute paths

    Returns:
        Path to the copied fixture directory
    """
    fixtures_dir = pathlib.Path(__file__).parent / "fixtures"
    source_dir = fixtures_dir / fixture_name
    dest_dir = tmp_path / fixture_name

    # Copy the entire fixture directory
    shutil.copytree(source_dir, dest_dir)

    # Update compile_commands.json to use absolute paths if requested
    compile_commands_file = dest_dir / "compile_commands.json"

    if not compile_commands_file.exists():
        return dest_dir

    with compile_commands_file.open("r") as f:
        compile_commands = json.load(f)
        if make_paths_absolute:
            for entry in compile_commands:
                directory_abs = (
                    pathlib.Path(dest_dir, entry["directory"]).resolve().as_posix()
                    if not os.path.isabs(entry["directory"])
                    else entry["directory"]
                )
                file_abs = (
                    pathlib.Path(directory_abs, entry["file"]).resolve().as_posix()
                    if not os.path.isabs(entry["file"])
                    else entry["file"]
                )
                # Ensure the command uses absolute paths if it contains the file path
                # This is necessary because we always generate absolute paths in replacement files,
                # while clang-tidy uses the paths from the compile command
                if entry["file"] in entry["command"]:
                    entry["command"] = entry["command"].replace(entry["file"], file_abs)
                entry["directory"] = directory_abs
                entry["file"] = file_abs

    with compile_commands_file.open("w") as f:
        json.dump(compile_commands, f, indent=4)

    return dest_dir


def assert_file_matches_expected(
    actual_file: pathlib.Path, expected_file: pathlib.Path
):
    """Assert that the actual file content matches the expected file content.

    Args:
        actual_file: Path to the file with actual content
        expected_file: Path to the file with expected content

    Raises:
        AssertionError: If file contents don't match
    """
    actual_content = actual_file.read_text(encoding="utf-8")
    expected_content = expected_file.read_text(encoding="utf-8")

    assert actual_content == expected_content, (
        f"File {actual_file} does not match expected content.\n"
        f"Expected file: {expected_file}"
    )
