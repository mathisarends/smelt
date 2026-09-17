from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from collections.abc import Mapping

DOCS_BASE_URL = "https://github.com/mathisarends/smelt/blob/main/docs/rules"


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    HINT = "hint"

    @property
    def rank(self) -> int:
        return {"error": 3, "warning": 2, "hint": 1}[self.value]

    def at_least(self, other: Severity) -> bool:
        return self.rank >= other.rank


class Category(StrEnum):
    DEPENDENCIES = "dependencies"
    CODE = "code"
    STRUCTURE = "structure"
    TESTS = "tests"
    META = "meta"


@dataclass(frozen=True, slots=True)
class ImportLink:
    importer: str
    imported: str
    line: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {"importer": self.importer, "imported": self.imported, "line": self.line}


@dataclass(frozen=True, slots=True)
class LineEdit:
    """Replace line ``line`` (1-based) of ``path``; ``text=None`` deletes it."""

    path: str
    line: int
    text: str | None


@dataclass(frozen=True, slots=True)
class FileMove:
    source: str
    destination: str


@dataclass(frozen=True, slots=True)
class FileWrite:
    """Create or overwrite ``path`` with ``content``."""

    path: str
    content: str


type Edit = LineEdit | FileMove | FileWrite


@dataclass(frozen=True, slots=True)
class Fix:
    description: str
    edits: tuple[Edit, ...]


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    rule: str
    severity: Severity
    message: str
    path: str | None = None
    line: int | None = None
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None
    source_module: str | None = None
    target_module: str | None = None
    import_chain: tuple[ImportLink, ...] = ()
    feature: str | None = None
    layer: str | None = None
    expected: Mapping[str, Any] | None = None
    hint: str | None = None
    fix: Fix | None = None
    fixable: bool = False
    docs_url: str | None = None
    category: Category | None = field(default=None, compare=False)

    def with_severity(self, severity: Severity) -> Self:
        return replace(self, severity=severity)

    def sort_key(self) -> tuple[str, int, int, str, str]:
        return (
            self.path or "",
            self.line or 0,
            self.column or 0,
            self.code,
            self.message,
        )

    def involved_modules(self) -> set[str]:
        modules = {m for m in (self.source_module, self.target_module) if m}
        for link in self.import_chain:
            modules.update((link.importer, link.imported))
        return modules

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "rule": self.rule,
            "severity": self.severity.value,
            "category": self.category.value if self.category else None,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "feature": self.feature,
            "layer": self.layer,
            "source_module": self.source_module,
            "target_module": self.target_module,
        }
        if self.import_chain:
            data["import_chain"] = [link.to_json() for link in self.import_chain]
        data["expected"] = dict(self.expected) if self.expected is not None else None
        data["hint"] = self.hint
        data["fixable"] = self.fixable or self.fix is not None
        data["docs_url"] = self.docs_url
        return data


def docs_url(code: str) -> str:
    return f"{DOCS_BASE_URL}/{code}.md"
