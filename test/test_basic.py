"""
Test suite for basic clangd-tidy operations.

This file contains parametrized tests that cover the core functionality
across multiple fixtures with different configurations.
"""

import pathlib
import pytest

from test_utils import (
    run_clangd_tidy,
    copy_fixture_directory,
    assert_file_matches_expected,
)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "basic",
        "utf8",
        "cross_file",
        "clangd_config",
        "tool_specific_config",
        "combined_config",
        "multiple_fixes",
    ],
)
def test_format_checking(tmp_path: pathlib.Path, fixture_name: str):
    """Test that --format detects formatting violations."""
    test_dir = copy_fixture_directory(fixture_name, tmp_path)

    source_files = sorted(test_dir.glob("*.cpp")) + sorted(test_dir.glob("*.h"))
    formatted_source_files = sorted(test_dir.glob("*.formatted"))

    for source_file in source_files + formatted_source_files:
        args = ["--format"]
        args.append(str(source_file))

        # Run with --format to check formatting (should detect violations in unformatted files)
        process = run_clangd_tidy(args, check=False)
        if source_file in formatted_source_files:
            # The file is already formatted, so --format should pass
            assert (
                process.returncode == 0
            ), f"Expected no formatting violations in {source_file}, but some were reported."
        else:
            # The file is unformatted, so --format should report violations
            assert (
                process.returncode != 0
            ), f"Expected formatting violations in {source_file}, but none were reported."


@pytest.mark.parametrize(
    "fixture_name",
    [
        "basic",
        "utf8",
        "cross_file",
        "clangd_config",
        "tool_specific_config",
        "combined_config",
        "multiple_fixes",
    ],
)
def test_fixes(tmp_path: pathlib.Path, fixture_name: str):
    """Test that --fix applies fixes correctly across different fixtures."""
    # cross_file needs absolute paths
    make_absolute = fixture_name == "cross_file"
    test_dir = copy_fixture_directory(
        fixture_name, tmp_path, make_paths_absolute=make_absolute
    )

    # Find all source files in fixture
    source_files = sorted(test_dir.glob("*.cpp")) + sorted(test_dir.glob("*.h"))

    run_clangd_tidy(
        [
            "--fix",
            "-p",
            str(test_dir),
            *[str(source_file) for source_file in source_files],
        ]
    )

    for source_file in source_files:
        assert_file_matches_expected(
            source_file, source_file.with_name(f"{source_file.name}.fixed")
        )


@pytest.mark.parametrize(
    "fixture_name",
    [
        "basic",
        "utf8",
        "cross_file",
        "clangd_config",
        "tool_specific_config",
        "combined_config",
    ],
)
def test_fix_with_format_style(tmp_path: pathlib.Path, fixture_name: str):
    """Test that --fix --format-style applies both fixes and formatting."""
    # cross_file needs absolute paths
    make_absolute = fixture_name == "cross_file"
    test_dir = copy_fixture_directory(
        fixture_name, tmp_path, make_paths_absolute=make_absolute
    )

    # Find all source files in fixture
    source_files = sorted(test_dir.glob("*.cpp")) + sorted(test_dir.glob("*.h"))

    run_clangd_tidy(
        [
            "--fix",
            "--format-style=file",
            "-p",
            str(test_dir),
            *[str(source_file) for source_file in source_files],
        ]
    )

    for source_file in source_files:
        assert_file_matches_expected(
            source_file, source_file.with_name(f"{source_file.name}.fixed.formatted")
        )
