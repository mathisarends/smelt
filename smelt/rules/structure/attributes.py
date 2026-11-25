"""Review public data attributes used only within their class or module."""

from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc


class InternalPublicAttribute(Rule):
    activation = POLICIES["VIS004"]
    code = "VIS004"
    name = "visibility/internal-public-attribute"
    category = Category.VISIBILITY
    default_severity = Severity.WARNING
    requires = frozenset({Index.MEMBERS})
    doc = RuleDoc(
        summary="A public instance or class data attribute has no observed access outside its visibility boundary.",
        rationale=(
            "Implementation state should not advertise a public interface solely because it lacks a leading underscore. This opt-in review covers direct instance assignments in ordinary methods and simple class data declarations on unambiguous top-level classes, including unused attributes. The default visibility boundary is the owning class. With public_attribute_boundary set to module, production reads and writes in the defining module count as internal access. Any same-named access outside the boundary conservatively preserves visibility, including unresolved receivers, writes, deletions and literal reflection. Test accesses count even when test-quality rules exclude the file; test-only reads remain the responsibility of VIS010, so these rules do not overlap. Private names, methods/properties, possible descriptors, decorated/inherited/subclassed classes, slots and custom attribute hooks are excluded. This also preserves dataclass, attrs, Pydantic, NamedTuple and TypedDict data contracts. Classes explicitly exported through __all__ or public package-facade bindings retain their public attributes, including aliased re-export chains. Inheritance is excluded as a whole rather than attempting to prove which fields a framework or base class owns."
            " Excluded files (including maintainer scripts), unselected packages, external consumers and arbitrary dynamic access are unassessed, not proven absent; usage_scope records the configured analysis boundary. This review does not prove that renaming is safe."
        ),
        bad="class Readiness:\n    def __init__(self, initial):\n        self.initial = initial\n    def select(self):\n        return self.initial",
        good="class Readiness:\n    def __init__(self, initial):\n        self._initial = initial\n    def select(self):\n        return self._initial",
        fix="Review whether the attribute belongs to the supported API. For internal state, add a leading underscore and update internal reads and writes, including same-module accesses in module mode. Check name collisions and dynamic consumers. If external callers need to read the value and writes should stay local, consider a read-only property backed by a private field. Preserve deliberate public contracts with a reasoned VIS004 suppression on the declaration or a package rule override. Suggested names avoid observed collisions. Smelt leaves renaming to the caller. Enable the rule in rules, select VIS004, or set public_attribute_boundary under style or in a style override.",
        canonical=(
            "Make data attributes used only inside their visibility boundary private"
        ),
        config=(
            "rules",
            "style.public_attribute_boundary",
            "style.overrides",
            "project.test_roots",
        ),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        boundary = ctx.contracts.public_attribute_boundary(target.module)
        return f"Make data attributes used only inside their {boundary} private"
