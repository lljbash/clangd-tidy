"""
Test suite for clangd-tidy --fix and --export-fixes features.
"""

import pathlib
import shutil
import subprocess
import yaml

from test_utils import (
    assert_file_matches_expected,
    copy_fixture_directory,
    run_clangd_tidy,
)


def test_fix_it(tmp_path: pathlib.Path):
    """Test that --fix applies changes directly to the file."""
    test_dir = copy_fixture_directory("basic", tmp_path)
    test_file = test_dir / "test_fix.cpp"
    expected_file = test_dir / "test_fix.cpp.fixed"

    # Run clangd-tidy to apply fixes directly
    run_clangd_tidy(["--verbose", "--fix", str(test_file)])

    assert_file_matches_expected(test_file, expected_file)


def test_export_fixes_parity(tmp_path: pathlib.Path):
    """Test that --export-fixes produces output compatible with clang-tidy."""
    # Copy the fixture to a temporary directory. Make paths absolute as clang-tidy itself doesn't handle relative paths well.
    test_dir = copy_fixture_directory("basic", tmp_path, make_paths_absolute=True)
    test_file = test_dir / "test_fix.cpp"

    # 1. Run clang-tidy to generate a baseline fixes file
    clang_tidy_executable = "clang-tidy"
    assert shutil.which(
        clang_tidy_executable
    ), f"{clang_tidy_executable} not found in PATH"

    clang_tidy_fixes_path = tmp_path / "clang_tidy_fixes.yaml"
    clang_tidy_command = [
        clang_tidy_executable,
        f"-p={test_dir}",
        f"--export-fixes={clang_tidy_fixes_path}",
        str(test_file),
    ]
    subprocess.run(clang_tidy_command, check=True, capture_output=True, text=True)

    # 2. Run clangd-tidy to generate its fixes file
    clangd_tidy_fixes_path = tmp_path / "clangd_tidy_fixes_2.yaml"
    run_clangd_tidy(["--export-fixes", str(clangd_tidy_fixes_path), str(test_file)])

    # 3. Compare the two YAML files
    with open(clang_tidy_fixes_path, "r") as f:
        clang_tidy_fixes = yaml.safe_load(f)

    with open(clangd_tidy_fixes_path, "r") as f:
        clangd_tidy_fixes_generated = yaml.safe_load(f)

    # We only care about the diagnostics, not the main source file
    # Sort the diagnostics by file offset and filepath to ensure consistent order
    def get_sort_key(
        diagnostic_entry: dict[str, dict[str, int | str]],
    ) -> tuple[int, str]:
        """Return (FileOffset, FilePath) tuple for stable sorting."""
        msg = diagnostic_entry["DiagnosticMessage"]
        assert "FileOffset" in msg and "FilePath" in msg
        assert isinstance(msg["FileOffset"], int)
        assert isinstance(msg["FilePath"], str)
        return (msg["FileOffset"], msg["FilePath"])

    clang_tidy_fixes["Diagnostics"].sort(key=get_sort_key)
    clangd_tidy_fixes_generated["Diagnostics"].sort(key=get_sort_key)
    assert clang_tidy_fixes["Diagnostics"] == clangd_tidy_fixes_generated["Diagnostics"]


def test_fix_it_from_different_directory(tmp_path: pathlib.Path):
    """Test that --fix works when running from a different directory."""
    test_dir = copy_fixture_directory("basic", tmp_path)
    test_file = test_dir / "test_fix.cpp"
    expected_file = test_dir / "test_fix.cpp.fixed"

    # Create a subdirectory to run the command from
    run_dir = tmp_path / "run_dir"
    run_dir.mkdir()

    # Run clangd-tidy from a different directory using absolute path
    run_clangd_tidy(["--fix", str(test_file.absolute())], cwd=run_dir)

    assert_file_matches_expected(test_file, expected_file)


def test_fix_across_files(tmp_path: pathlib.Path):
    """Test that --fix works across multiple files (header + source).

    When analyzing a header file, clangd should report naming convention violations
    and provide fixes not just for the header, but also for all source files that use it
    (as long as they're in the clangd index).
    """
    # Copy the fixture to a temporary directory. We need absolute paths in compile_commands for clangd to resolve cross-file references.
    test_dir = copy_fixture_directory("cross_file", tmp_path, make_paths_absolute=True)
    header_file = test_dir / "math_utils.h"
    source_file = test_dir / "math_utils.cpp"
    header_expected = test_dir / "math_utils.h.fixed"
    source_expected = test_dir / "math_utils.cpp.fixed"

    # Run --fix on the HEADER file only
    # Clangd should detect the violation in the header and provide fixes for both
    # the header declaration and the source file usage
    run_clangd_tidy(["--fix", "-p", str(test_dir), str(header_file)])

    # Verify fixes were applied to both files
    assert_file_matches_expected(header_file, header_expected)
    assert_file_matches_expected(source_file, source_expected)


def test_fix_it_with_clangd_config(tmp_path: pathlib.Path):
    """Test that clangd-tidy respects .clangd configuration files."""
    test_dir = copy_fixture_directory("clangd_config", tmp_path)
    test_file = test_dir / "test_fix.cpp"
    expected_file = test_dir / "test_fix.cpp.fixed"

    # Run clangd-tidy to apply fixes
    run_clangd_tidy(["--fix", str(test_file)])

    assert_file_matches_expected(test_file, expected_file)


def test_error_recovery_continue_after_failed_command(tmp_path: pathlib.Path):
    """Test that clangd-tidy continues processing after a command error.

    This test uses a file with TWO naming violations that will result in
    two separate fix commands:
    1. A namespace naming violation (unsupported - executes first, fails with error)
    2. A function naming violation (supported - executes second, succeeds)

    The test verifies our intelligent error handling:
    - Both commands are sent to clangd (queued and executed)
    - The namespace rename fails with an error response from clangd
    - The error is logged and reported to the user
    - We recover, restart clangd and continue processing the second command
    - The function fix IS applied despite the namespace error and a clangd crash

    This tests that we don't skip valid commands just because another command fails.
    """
    test_dir = copy_fixture_directory("error_recovery", tmp_path)
    test_file = test_dir / "test_crash.cpp"
    expected_file = test_dir / "test_crash.cpp.fixed"

    process = run_clangd_tidy(["--fix", str(test_file)])

    # Verify that the error was logged
    assert (
        "Cannot rename symbol" in process.stderr or "Cannot apply fix" in process.stderr
    ), "Should mention the error for namespace rename in stderr"

    assert_file_matches_expected(test_file, expected_file)


def test_utf8_encoding(tmp_path: pathlib.Path):
    """Test that fixes work correctly with UTF-8 encoded files containing non-ASCII characters.

    This verifies that character offset calculations handle multi-byte UTF-8 sequences correctly.
    LSP spec uses UTF-16 code units, but clangd can use UTF-8 in practice.

    The critical test case: UTF-8 multi-byte characters appearing BEFORE the replacement
    position on the same line. This ensures byte offset calculations account for the
    difference between character count and byte count (café: 5 chars/6 bytes, 日本語: 3 chars/9 bytes).
    """
    test_dir = copy_fixture_directory("utf8", tmp_path)
    test_file = test_dir / "test_utf8.cpp"
    expected_file = test_dir / "test_utf8.cpp.fixed"

    # Run clangd-tidy to apply fixes, specifying the test directory for compile_commands.json
    run_clangd_tidy(["--verbose", "--fix", "-p", str(test_dir), str(test_file)])

    assert_file_matches_expected(test_file, expected_file)
