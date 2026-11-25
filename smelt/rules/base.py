from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from smelt.diagnostics.fixes import Fix, FixAvailability
from smelt.diagnostics.violation import (
    Category,
    ImportLink,
    Severity,
    Violation,
    docs_url,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from smelt.analysis.context import AnalysisContext, Index
    from smelt.model import ModuleInfo
    from smelt.rules.activation import PolicyActivation


@dataclass(frozen=True, slots=True)
class RuleDoc:
    summary: str
    rationale: str
    bad: str
    good: str
    fix: str
    canonical: str
    """One imperative instruction for writing conforming code, shown by
    ``smelt context --guide``. Rules with options render it from their policy."""
    config: tuple[str, ...] = ()
    example_language: str = "python"


@dataclass(frozen=True, slots=True)
class GuideTarget:
    """The module a guide is written for; ``module`` is empty for the project."""

    module: str
    path: str | None = None
    info: ModuleInfo | None = None

    def absolute(self, root: Path) -> Path | None:
        return root / self.path if self.path else None


@runtime_checkable
class RuleContract(Protocol):
    """Rule metadata and guidance, including lazy rule implementations.

    The native core finds the violations of every built-in rule.
    """

    code: str
    name: str
    category: Category
    default_severity: Severity
    requires: frozenset[Index]

    def explain(self) -> RuleDoc:
        """Return the documentation shown by `smelt explain` and the rule pages."""

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        """Return the instruction for writing ``target``; None when nothing applies."""


class Rule:
    """A diagnostic rule with shared metadata and violation construction."""

    code: str
    name: str
    category: Category
    default_severity: Severity = Severity.ERROR
    requires: frozenset[Index] = frozenset()
    enabled_by_default: ClassVar[bool] = False
    activation: ClassVar[PolicyActivation | None] = None
    scope: ClassVar[str] = "source"
    fix_availability: ClassVar[FixAvailability] = FixAvailability.NONE
    """Whether findings carry a machine-readable Fix; published in the catalog."""
    architecture_scoped: ClassVar[bool] = False
    """Reads architecture policy beyond module classification, so a workspace
    with member architectures runs it once per architecture."""
    doc: ClassVar[RuleDoc]

    def explain(self) -> RuleDoc:
        return self.doc

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        """The static instruction; rules whose options change it override this."""
        del ctx, target
        return self.doc.canonical

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
        edge: str | None = None,
        fix: Fix | None = None,
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
            edge=edge,
            docs_url=docs_url(self.code),
            category=self.category,
            fix=fix,
        )


@dataclass(slots=True)
class RuleSet:
    rules: list[RuleContract] = field(default_factory=list)

    def __post_init__(self) -> None:
        codes: set[str] = set()
        names: set[str] = set()
        for rule in self.rules:
            if rule.code in codes:
                raise ValueError(f"duplicate rule code: {rule.code}")
            if rule.name in names:
                raise ValueError(f"duplicate rule name: {rule.name}")
            codes.add(rule.code)
            names.add(rule.name)

    def by_code(self, code: str) -> RuleContract | None:
        return next((r for r in self.rules if r.code == code), None)

    def lookup(self, code_or_name: str) -> RuleContract | None:
        wanted = code_or_name.strip()
        return next(
            (r for r in self.rules if wanted.upper() == r.code or wanted == r.name),
            None,
        )

    def family_codes(self, selector: str) -> tuple[str, ...] | None:
        """Codes of a family selected as `modeling` or `modeling/`, else None."""
        wanted = selector.strip().lower().removesuffix("/")
        if wanted not in Category:
            return None
        return tuple(rule.code for rule in self.rules if rule.category == wanted)

    def matching_codes(self, selector: str) -> tuple[str, ...]:
        """Resolve a name, family, code prefix or ALL against this rule set."""
        wanted = selector.strip()
        if (family := self.family_codes(wanted)) is not None:
            return family
        if rule := self.lookup(wanted.lower()):
            return (rule.code,)
        prefix = wanted.upper()
        return tuple(
            rule.code
            for rule in self.rules
            if prefix and (prefix == "ALL" or rule.code.startswith(prefix))
        )
