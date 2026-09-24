from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from smelt.diagnostics.violation import Category, Severity, Violation

type LineReader = Callable[[str, int], str]


@dataclass(frozen=True, slots=True)
class RuleMeta:
    code: str
    name: str
    category: Category
    default_severity: Severity
    enabled_by_default: bool
    summary: str
    docs_url: str

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "category": self.category.value,
            "default_severity": self.default_severity.value,
            "enabled_by_default": self.enabled_by_default,
            "summary": self.summary,
            "docs_url": self.docs_url,
        }


@dataclass
class Report:
    violations: list[Violation]
    modules: int
    categories: list[Category]
    rules: list[RuleMeta] = field(default_factory=list)
    fail_on: Severity = Severity.ERROR
    suppressed: int = 0
    baselined: int = 0

    def count(self, severity: Severity) -> int:
        return sum(1 for v in self.violations if v.severity is severity)

    @property
    def failed(self) -> bool:
        return any(v.severity.at_least(self.fail_on) for v in self.violations)

    @property
    def status(self) -> str:
        return "failed" if self.failed else "passed"

    def summary_json(self) -> dict[str, int]:
        return {
            "errors": self.count(Severity.ERROR),
            "warnings": self.count(Severity.WARNING),
            "hints": self.count(Severity.HINT),
            "modules": self.modules,
            "suppressed": self.suppressed,
            "baselined": self.baselined,
        }
