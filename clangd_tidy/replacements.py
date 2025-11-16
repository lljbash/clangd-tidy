from __future__ import annotations
from functools import cache
import logging
import pathlib
from typing import List, Optional, Tuple

from attrs import define

from .lsp.messages import Diagnostic, Position, WorkspaceEdit, uri_to_path


@define
class Replacement:
    FilePath: str
    Offset: int
    Length: int
    ReplacementText: str


@define
class DiagnosticMessage:
    Message: str
    FilePath: str
    FileOffset: int
    Replacements: List[Replacement]


@define
class ClangApplyReplacementsDiagnostic:
    DiagnosticName: str
    DiagnosticMessage: DiagnosticMessage
    Level: str
    BuildDirectory: str


@cache
def _get_line_offsets(
    path: pathlib.Path,
) -> Optional[List[int]]:
    """Get or compute line start byte offsets for a file.

    Returns list where index i contains the byte offset of line i's first character.
    With UTF-8 encoding negotiated, we can convert LSP Position to absolute byte offset
    using: absolute_offset = line_offsets[position.line] + position.character

    Args:
        path: Path to the file

    Returns:
        List of byte offsets for each line, or None if file cannot be read/decoded
    """
    # Read file as bytes (avoid decode/re-encode overhead)
    try:
        file_bytes = path.read_bytes()
    except OSError as e:
        logging.warning(f"cannot read file {path}: {e}")
        return None

    # Verify it's valid UTF-8
    try:
        file_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        logging.warning(
            f"cannot decode file {path} as utf-8, skipping fixes for it: {e}"
        )
        return None

    # Build byte offset table by scanning for newlines
    # Use bytes.split() which is C-implemented and fast
    line_offsets = [0]
    offset = 0
    for line in file_bytes.split(b"\n"):
        offset += len(line) + 1  # +1 for the \n separator
        line_offsets.append(offset)
    line_offsets.pop()  # Remove final entry (no line after last \n or EOF)

    return line_offsets


def _position_to_offset(line_offsets: List[int], position: Position) -> int:
    """Convert LSP Position to absolute byte offset.

    With UTF-8 encoding, Position.character is a byte count from line start.
    For positions beyond EOF, clamps to the last valid offset.

    Args:
        line_offsets: Byte offsets for each line (from _get_line_offsets)
        position: LSP Position to convert

    Returns:
        Absolute byte offset in the file
    """
    # Clamp line to valid range (handle edits at/beyond EOF)
    line = min(position.line, len(line_offsets) - 1)
    return line_offsets[line] + position.character


def create_replacements(
    edits: List[Tuple[Diagnostic, WorkspaceEdit]],
) -> List[ClangApplyReplacementsDiagnostic]:
    """Create a list of replacements from the given edits.

    Converts LSP WorkspaceEdit objects into clang-apply-replacements format.
    Handles:
    - Cross-file refactorings (edits may span multiple files)
    - UTF-8 encoding and character offset calculations
    - Diagnostic grouping (all edits from one diagnostic share MainSourceFile)

    Args:
        edits: List of (Diagnostic, WorkspaceEdit) pairs to convert

    Returns:
        List of replacement dictionaries in clang-apply-replacements format
    """
    all_replacements: List[ClangApplyReplacementsDiagnostic] = []

    for diag, edit in edits:
        if not edit.changes:
            continue

        # Collect replacements across all files for this diagnostic
        replacements: List[Replacement] = []
        first_path = None
        first_offset = None

        for uri, text_edits in edit.changes.items():
            path = uri_to_path(uri)

            line_offsets = _get_line_offsets(path)
            if line_offsets is None:
                continue

            for text_edit in text_edits:
                # Convert LSP positions to absolute byte offsets
                start_offset = _position_to_offset(line_offsets, text_edit.range.start)
                end_offset = _position_to_offset(line_offsets, text_edit.range.end)

                replacements.append(
                    Replacement(
                        FilePath=str(path),
                        Offset=start_offset,
                        Length=end_offset - start_offset,
                        ReplacementText=text_edit.newText,
                    )
                )

                # Track first file and offset for diagnostic header
                # clang-apply-replacements format requires each diagnostic to have:
                # - MainSourceFile: where the diagnostic was reported
                # - FileOffset: byte position in that file
                # This is used for grouping/sorting diagnostics in output
                if first_path is None:
                    first_path = path
                    first_offset = start_offset

        # Transform diagnostic message for clang-apply-replacements format
        # Remove clangd's " (fix available)" suffix
        message = diag.message.replace(" (fix available)", "")
        # Convert first letter to lowercase (clang-tidy convention)
        if message and message[0].isupper():
            message = message[0].lower() + message[1:]

        # Map severity levels to match clang-tidy output
        severity = diag.severity.name.capitalize() if diag.severity else "Warning"
        # Special case: write all Hints as Warnings, as Hints break clang-apply-replacements
        if severity == "Hint":
            severity = "Warning"

        all_replacements.append(
            ClangApplyReplacementsDiagnostic(
                DiagnosticName=diag.code,
                DiagnosticMessage=DiagnosticMessage(
                    Message=message,
                    FilePath=str(first_path) if first_path else "",
                    FileOffset=first_offset if first_offset is not None else 0,
                    Replacements=replacements,
                ),
                Level=severity,
                BuildDirectory=str(first_path.parent) if first_path else "",
            )
        )
    return all_replacements
