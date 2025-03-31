# clangd-tidy: A Faster Alternative to clang-tidy

## Motivation

[clang-tidy](https://clang.llvm.org/extra/clang-tidy/) is a powerful tool for static analysis of C++ code. However, it's [widely acknowledged](https://www.google.com/search?q=clang-tidy+slow) that clang-tidy takes a significant amount of time to run on large codebases, particularly when enabling numerous checks. This often leads to the dilemma of disabling valuable checks to expedite clang-tidy execution.

In contrast, [clangd](https://clangd.llvm.org/), the language server with built-in support for clang-tidy, has been [observed](https://stackoverflow.com/questions/76531831/why-is-clang-tidy-in-clangd-so-much-faster-than-run-clang-tidy-itself) to be significantly faster than clang-tidy when running the same checks. It provides diagnostics almost instantly upon opening a file in your editor. The key distinction lies in the fact that clang-tidy checks the codes from all headers (although it suppresses the warnings from them by default), whereas clangd only builds AST from these headers.

Unfortunately, there seems to be no plan within LLVM to accelerate the standalone version of clang-tidy. This project addresses this by offering a faster alternative to clang-tidy, leveraging the speed of clangd. It acts as a wrapper for clangd, running it in the background and collecting diagnostics. Designed as a drop-in replacement for clang-tidy, it seamlessly integrates with existing build systems and CI scripts.

## Comparison with clang-tidy

**Pros:**

- clangd-tidy is significantly faster than clang-tidy (over 10x in my experience).
- clangd-tidy can check header files individually, even if they are not included in the compilation database.
- clangd-tidy groups diagnostics by files -- no more duplicated diagnostics from the same header!
- clangd-tidy provides an optional code format checking feature, eliminating the need to run clang-format separately.
- clangd-tidy supports [`.clangd` configuration files](https://clangd.llvm.org/config), offering features not supported by clang-tidy.
  - Example: Removing unknown compiler flags from the compilation database.
    ```yaml
    CompileFlags:
      Remove: -fabi*
    ```
  - Example: Adding IWYU include checks.
    ```yaml
    Diagnostics:
      # Available in clangd-14
      UnusedIncludes: Strict
      # Require clangd-17
      MissingIncludes: Strict
    ```
- Hyperlinks on diagnostic check names in supported terminals.
- Refer to [Usage](#usage) for more features.

**Cons:**

- clangd-tidy lacks support for the `--fix` option. (Consider using code actions provided by your editor if you have clangd properly configured, as clangd-tidy is primarily designed for speeding up CI checks.)
- clangd-tidy silently disables [several](https://searchfox.org/llvm/rev/cb7bda2ace81226c5b33165411dd0316f93fa57e/clang-tools-extra/clangd/TidyProvider.cpp#199-227) checks not supported by clangd.
- Diagnostics generated by clangd-tidy might be marginally less aesthetically pleasing compared to clang-tidy.

## Prerequisites

- [clangd](https://clangd.llvm.org/)
- Python 3.8+ (may work on older versions, but not tested)
- [attrs](https://www.attrs.org/) and [cattrs](https://catt.rs/) (automatically installed if clangd-tidy is installed via pip)
- [tqdm](https://github.com/tqdm/tqdm) (optional, required for progress bar support)

## Installation

```bash
pip install clangd-tidy
```

## Usage

### clang-tidy

```
usage: clangd-tidy [--allow-extensions ALLOW_EXTENSIONS]
                   [--fail-on-severity SEVERITY] [-f] [-o OUTPUT]
                   [--line-filter LINE_FILTER] [--tqdm] [--github]
                   [--git-root GIT_ROOT] [-c] [--context CONTEXT]
                   [--color {auto,always,never}] [-v]
                   [-p COMPILE_COMMANDS_DIR] [-j JOBS]
                   [--clangd-executable CLANGD_EXECUTABLE]
                   [--query-driver QUERY_DRIVER] [-V] [-h]
                   filename [filename ...]

Run clangd with clang-tidy and output diagnostics. This aims to serve as a
faster alternative to clang-tidy.

input options:
  filename              Files to analyze. Ignores files with extensions not
                        listed in ALLOW_EXTENSIONS.
  --allow-extensions ALLOW_EXTENSIONS
                        A comma-separated list of file extensions to allow.
                        [default: c,h,cpp,cc,cxx,hpp,hh,hxx,cu,cuh]

check options:
  --fail-on-severity SEVERITY
                        Specifies the diagnostic severity level at which the
                        program exits with a non-zero status. Possible values:
                        error, warn, info, hint. [default: hint]
  -f, --format          Also check code formatting with clang-format. Exits
                        with a non-zero status if any file violates formatting
                        rules.

output options:
  -o OUTPUT, --output OUTPUT
                        Output file for diagnostics. [default: stdout]
  --line-filter LINE_FILTER
                        A JSON with a list of files and line ranges that will
                        act as a filter for diagnostics. Compatible with
                        clang-tidy --line-filter parameter format.
  --tqdm                Show a progress bar (tqdm required).
  --github              Append workflow commands for GitHub Actions to output.
  --git-root GIT_ROOT   Specifies the root directory of the Git repository.
                        Only works with --github. [default: current directory]
  -c, --compact         Print compact diagnostics (legacy).
  --context CONTEXT     Number of additional lines to display on both sides of
                        each diagnostic. This option is ineffective with
                        --compact. [default: 2]
  --color {auto,always,never}
                        Colorize the output. This option is ineffective with
                        --compact. [default: auto]
  -v, --verbose         Stream verbose output from clangd to stderr.

clangd options:
  -p COMPILE_COMMANDS_DIR, --compile-commands-dir COMPILE_COMMANDS_DIR
                        Specify a path to look for compile_commands.json. If
                        the path is invalid, clangd will look in the current
                        directory and parent paths of each source file.
                        [default: build]
  -j JOBS, --jobs JOBS  Number of async workers used by clangd. Background
                        index also uses this many workers. [default: 1]
  --clangd-executable CLANGD_EXECUTABLE
                        Clangd executable. [default: clangd]
  --query-driver QUERY_DRIVER
                        Comma separated list of globs for white-listing gcc-
                        compatible drivers that are safe to execute. Drivers
                        matching any of these globs will be used to extract
                        system includes. e.g.
                        `/usr/bin/**/clang-*,/path/to/repo/**/g++-*`.

generic options:
  -V, --version         Show program's version number and exit.
  -h, --help            Show this help message and exit.

Find more information on https://github.com/lljbash/clangd-tidy.
```

### clangd-tidy-diff

```
usage: clangd-tidy-diff [-h] [-V] [-p COMPILE_COMMANDS_DIR]
                        [--pass-arg PASS_ARG]

Run clangd-tidy on modified files, reporting diagnostics only for changed lines.

optional arguments:
  -h, --help            show this help message and exit
  -V, --version         show program's version number and exit
  -p COMPILE_COMMANDS_DIR, --compile-commands-dir COMPILE_COMMANDS_DIR
                        Specify a path to look for compile_commands.json. If
                        the path is invalid, clangd-tidy will look in the
                        current directory and parent paths of each source
                        file.
  --pass-arg PASS_ARG   Pass this argument to clangd-tidy (can be used
                        multiple times)

Receives a diff on stdin and runs clangd-tidy only on the changed lines.
This is useful to slowly onboard a codebase to linting or to find regressions.
Inspired by clang-tidy-diff.py from the LLVM project.

Example usage with git:
    git diff -U0 HEAD^^..HEAD | clangd-tidy-diff -p my/build

```

## Acknowledgement

Special thanks to [@yeger00](https://github.com/yeger00) for his [pylspclient](https://github.com/yeger00/pylspclient), which inspired earlier versions of this project.

A big shoutout to [clangd](https://clangd.llvm.org/) and [clang-tidy](https://clang.llvm.org/extra/clang-tidy/) for their great work!

Claps to
- [@ArchieAtkinson](https://github.com/ArchieAtkinson) for his artistic flair in the fancy diagnostic formatter.
- [@jmpfar](https://github.com/jmpfar) for his contribution to hyperlink support and `clangd-tidy-diff`.

Contributions are welcome! Feel free to open an issue or a pull request.
