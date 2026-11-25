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
