# Test Fixtures

This directory contains pre-created test fixtures for the clangd-tidy test suite. Each fixture directory is self-contained with source files, configuration files, and a compilation database. The source code files can be viewed directly in an IDE to see diagnostics detected by clangd via LSP and also try out the fixits manually.

## Structure

Each fixture directory contains:
- Source files (`.cpp`, `.h`)
- Configuration files (a combination of `.clang-tidy`, `.clangd`, `.clang-format`)
- Compilation database (`compile_commands.json`)
- For `--fix` tests: `*.fixed` files for each source file
- For `--fix --format-style` tests: `*.fixed.formatted`

### Compile commands

The `compile_commands.json` files use relative paths to make directories easily copyable. For some tests we need absolute paths, which can be automatically converted to by using `make_paths_absolute=True` when calling `copy_fixture_directory`.

### Code formatting
Notes: Standard use of `--format-style` with `clang-tidy` assumes the input files were already formatted correctly, and only formats parts of code affected by `--fix`. Given that it's not trivial to ensure code formatting would have any effect after fixes on a well-formatted input code, we use a misformatted input code and only have local formatting changes in `*.fixed.formatted` files.

## Usage

Tests use the `copy_fixture(fixture_name, tmp_path)` helper function:

The `copy_fixture()` function:
1. Copies the entire fixture directory to `tmp_path`
2. If `make_paths_absolute` is set to `True`, replaces all relative paths in `compile_commands.json` with absolute paths (using `tmp_path` from step 1.)
3. Returns the path to the copied directory
