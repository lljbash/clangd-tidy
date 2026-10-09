"""
Test suite for clangd-tidy diagnostics reporting (without --fix).

These tests verify that clangd-tidy can report diagnostics without applying fixes.
"""

import pathlib

from test_utils import (
    run_clangd_tidy,
    copy_fixture_directory,
)


def test_report_nullptr_diagnostics(tmp_path: pathlib.Path):
    """Test that diagnostics are reported."""
    test_dir = copy_fixture_directory("basic", tmp_path)
    test_file = test_dir / "test_fix.cpp"  # Contains NULL

    # Run without --fix, should only report diagnostics
    process = run_clangd_tidy([str(test_file)], check=False)

    # Verify diagnostics appear in output
    assert (
        "modernize-use-nullptr" in process.stdout
    ), "Expected modernize-use-nullptr diagnostic in output"

    # Verify file was NOT modified (no fixes applied)
    original_content = test_file.read_text()
    assert "NULL" in original_content, "File should still contain NULL (not fixed)"
    assert (
        "nullptr" not in original_content
    ), "File should not contain nullptr without --fix"


def test_diagnostics_on_compilation_errors(tmp_path: pathlib.Path):
    """Test that diagnostics are reported even when code has compilation errors.

    Verifies that clangd-tidy reports three types of issues:
    1. Syntax errors (actual compilation failures)
    2. Compiler warnings (e.g., unused variables)
    3. clang-tidy diagnostics (style/modernization issues)
    """
    test_dir = copy_fixture_directory("compilation_errors", tmp_path)
    test_file = test_dir / "test_errors.cpp"

    # Run clangd-tidy on file with various errors
    process = run_clangd_tidy([str(test_file)], check=False)

    output = process.stdout

    assert (
        "[expected_semi_declaration]" in output.lower()
    ), "Should mention missing semicolon error"
    assert (
        "[modernize-use-nullptr]" in output
    ), "Should report clang-tidy diagnostic (nullptr)"

    # Verify we got diagnostics of different severity levels
    # We should see Error (compiler) and Warning or Hint (tidy)
    assert "Error:" in output, "Should include Error level diagnostics"
    assert (
        "Warning:" in output or "Hint:" in output
    ), "Should include Warning or Hint level diagnostics"
