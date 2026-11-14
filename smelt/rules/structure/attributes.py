"""Review public data attributes with no observed access outside their class."""

from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.member_names import private_name, rename_advice

if TYPE_CHECKING:
    from collections.abc import Iterator


class InternalPublicAttribute(Rule):
    code = "SMT335"
    name = "internal-public-attribute"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.MEMBERS})
    doc = RuleDoc(
        summary="A public instance or class data attribute has no observed access outside its class.",
        rationale=(
            "Implementation state should not advertise a public interface solely because it lacks a leading underscore. This opt-in review covers direct instance assignments in ordinary methods and simple class data declarations on unambiguous top-level classes, including unused attributes. Any same-named access outside the owning class conservatively preserves visibility, including unresolved receivers, writes, deletions and literal reflection. Test accesses count even when test-quality rules exclude the file; test-only reads remain the responsibility of SMT430, so these rules do not overlap. Private names, methods/properties, possible descriptors, decorated/inherited/subclassed classes, slots and custom attribute hooks are excluded. This also preserves dataclass, attrs, Pydantic, NamedTuple and TypedDict data contracts. Classes explicitly exported through __all__ or public package-facade bindings retain their public attributes, including aliased re-export chains. Inheritance is excluded as a whole rather than attempting to prove which fields a framework or base class owns."
            " Excluded files (including maintainer scripts), unselected packages, external consumers and arbitrary dynamic access are unassessed, not proven absent; usage_scope records the configured analysis boundary. This review does not prove that renaming is safe."
        ),
        bad="class Readiness:\n    def __init__(self, initial):\n        self.initial = initial\n    def select(self):\n        return self.initial",
        good="class Readiness:\n    def __init__(self, initial):\n        self._initial = initial\n    def select(self):\n        return self._initial",
        fix="Review whether the attribute belongs to the supported API. For internal state, add a leading underscore and update internal reads and writes, checking name collisions and dynamic consumers. Preserve deliberate public contracts with `# smelt: ignore[SMT335] -- <specific API reason>` on the reported assignment or declaration, or a package rule override. Suggested names avoid observed collisions; no automatic rename is performed. Enable rules: {internal-public-attribute: warning} or select --select SMT335.",
        config=("rules", "structure.contracts", "project.test_roots"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for attribute in ctx.member_usage.internal_attributes():
            qualified = f"{attribute.owner.name}.{attribute.name}"
            suggested_name = private_name(attribute.name, attribute.occupied_names)
            yield self.violation(
                f"{qualified} is public, but has no observed access outside its class",
                path=attribute.path,
                line=attribute.node.lineno,
                column=attribute.node.col_offset + 1,
                source_module=attribute.module,
                target_module=f"{attribute.module}.{qualified}",
                expected={
                    "class": attribute.owner.name,
                    "attribute": attribute.name,
                    "kind": attribute.kind,
                    "suggested_name": suggested_name,
                    "usage_scope": ctx.member_usage.usage_scope,
                    "internal_references": list(attribute.internal_references),
                    "automatic_rename": False,
                },
                hint=f"Unless public by design, {rename_advice(attribute.name, suggested_name)}; otherwise suppress SMT335 with a reason on the declaration.",
            )
