from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from smelt.diagnostics.violation import (
    Category,
    ImportLink,
    Severity,
    Violation,
    docs_url,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from smelt.analysis.context import AnalysisContext, Index


@dataclass(frozen=True, slots=True)
class RuleDoc:
    summary: str
    rationale: str
    bad: str
    good: str
    fix: str
    config: tuple[str, ...] = ()


@runtime_checkable
class Rule(Protocol):
    code: str
    name: str
    category: Category
    default_severity: Severity
    requires: frozenset[Index]

    def check(self, ctx: AnalysisContext) -> Iterable[Violation]: ...

    def explain(self) -> RuleDoc: ...


class BaseRule:
    """Convenience base: fills in rule metadata on every violation."""

    code: str
    name: str
    category: Category
    default_severity: Severity = Severity.ERROR
    requires: frozenset[Index] = frozenset()
    enabled_by_default: ClassVar[bool] = True
    doc: ClassVar[RuleDoc]

    def check(self, ctx: AnalysisContext) -> Iterable[Violation]:
        raise NotImplementedError

    def explain(self) -> RuleDoc:
        return self.doc

    def violation(  # noqa: PLR0913 - mirrors the Violation fields
        self,
        message: str,
        *,
        path: str | None = None,
        line: int | None = None,
        column: int | None = None,
        end_line: int | None = None,
        end_column: int | None = None,
        source_module: str | None = None,
        target_module: str | None = None,
        import_chain: Iterable[ImportLink] = (),
        feature: str | None = None,
        layer: str | None = None,
        expected: Mapping[str, Any] | None = None,
        hint: str | None = None,
    ) -> Violation:
        return Violation(
            code=self.code,
            rule=self.name,
            severity=self.default_severity,
            message=message,
            path=path,
            line=line,
            column=column,
            end_line=end_line if end_line is not None else (line if column else None),
            end_column=end_column,
            source_module=source_module,
            target_module=target_module,
            import_chain=tuple(import_chain),
            feature=feature,
            layer=layer,
            expected=expected,
            hint=hint,
            docs_url=docs_url(self.code),
            category=self.category,
        )


@dataclass(frozen=True, slots=True)
class RuleInfo:
    code: str
    name: str
    category: Category
    default_severity: Severity
    enabled_by_default: bool
    summary: str

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "category": self.category.value,
            "default_severity": self.default_severity.value,
            "enabled_by_default": self.enabled_by_default,
            "summary": self.summary,
        }


def rule_info(rule: Rule) -> RuleInfo:
    return RuleInfo(
        code=rule.code,
        name=rule.name,
        category=rule.category,
        default_severity=rule.default_severity,
        enabled_by_default=bool(getattr(rule, "enabled_by_default", True)),
        summary=rule.explain().summary,
    )


@dataclass
class RuleSet:
    rules: list[Rule] = field(default_factory=list)

    def by_code(self, code: str) -> Rule | None:
        return next((r for r in self.rules if r.code == code), None)

    def lookup(self, code_or_name: str) -> Rule | None:
        wanted = code_or_name.strip()
        return next(
            (r for r in self.rules if wanted.upper() == r.code or wanted == r.name),
            None,
        )
