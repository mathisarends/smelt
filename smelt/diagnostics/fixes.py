"""Machine-readable source edits that repair a finding."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Applicability(StrEnum):
    """Who may apply a fix: --fix, --fix --unsafe-fixes, or only the reader."""

    SAFE = "safe"
    UNSAFE = "unsafe"
    DISPLAY = "display"


class FixAvailability(StrEnum):
    """Whether a rule's findings carry a fix, published in the rule catalog."""

    ALWAYS = "always"
    SOMETIMES = "sometimes"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class TextEdit:
    """Replace `original` between two positions with `replacement`.

    Positions follow Violation: 1-based lines and 1-based UTF-8 byte columns, the
    end exclusive, in source with normalized line endings.
    """

    line: int
    column: int
    end_line: int
    end_column: int
    original: str
    replacement: str

    @classmethod
    def between(
        cls,
        before: str,
        after: str,
        *,
        span: tuple[int, int] | None = None,
        whole_lines: bool = False,
    ) -> TextEdit:
        """The edit turning `before` into `after`, covering at least `span`.

        `span` holds character offsets into `before`; `whole_lines` widens the
        edit to complete lines so a reader sees whole statements.
        """
        limit = min(len(before), len(after))
        prefix = 0
        while prefix < limit and before[prefix] == after[prefix]:
            prefix += 1
        suffix = 0
        while (
            suffix < limit - prefix
            and before[len(before) - suffix - 1] == after[len(after) - suffix - 1]
        ):
            suffix += 1
        if span is not None:
            prefix = min(prefix, span[0])
            suffix = min(suffix, len(before) - span[1])
        if whole_lines:
            prefix = before.rfind("\n", 0, prefix) + 1
            stop = len(before) - suffix
            if stop and before[stop - 1] != "\n":
                newline = before.find("\n", stop)
                suffix = 0 if newline < 0 else len(before) - newline - 1
        start, end = prefix, len(before) - suffix
        line, column = _position(before, start)
        end_line, end_column = _position(before, end)
        return cls(
            line,
            column,
            end_line,
            end_column,
            before[start:end],
            after[start : len(after) - suffix],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "original": self.original,
            "replacement": self.replacement,
        }


@dataclass(frozen=True, slots=True)
class Fix:
    """Edits to one file, applied all or nothing."""

    applicability: Applicability
    description: str
    edits: tuple[TextEdit, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "applicability": self.applicability.value,
            "description": self.description,
            "edits": [edit.to_json() for edit in self.edits],
        }


def source_offset(text: str, line: int, byte_column: int) -> int:
    """The character offset of an AST position (1-based line, 0-based UTF-8 column)."""
    start = 0
    for _ in range(line - 1):
        start = text.index("\n", start) + 1
    end = text.find("\n", start)
    current = text[start:] if end < 0 else text[start:end]
    return start + len(current.encode("utf-8")[:byte_column].decode("utf-8"))


def _position(text: str, offset: int) -> tuple[int, int]:
    line_start = text.rfind("\n", 0, offset) + 1
    column = len(text[line_start:offset].encode("utf-8")) + 1
    return text.count("\n", 0, offset) + 1, column
