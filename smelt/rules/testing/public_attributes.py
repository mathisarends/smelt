from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.member_names import private_name

if TYPE_CHECKING:
    from collections.abc import Iterator


class TestOnlyPublicAttribute(BaseRule):
    code = "SMT430"
    name = "test-only-public-attribute"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.MEMBERS, Index.TESTS})
    doc = RuleDoc(
        summary="A public attribute is read from tests but has no observed production access outside its class.",
        rationale=(
            "Exposing implementation state just for tests enlarges the apparent API and couples tests to internal representation. This opt-in review covers direct instance assignments in ordinary methods and simple class data declarations on unambiguous top-level classes. Reads by analyzed tests are resolved through known constructors, annotations, fixtures and class aliases/re-exports. Internal class accesses do not justify public visibility. Any same-named production attribute access outside the owning class, including unresolved receivers and literal reflection, conservatively preserves visibility. Private names, methods/properties, descriptors, decorated/inherited/subclassed classes, slots and custom attribute hooks are excluded. Test-quality exclusions apply to test evidence; test writes alone do not trigger a finding. External consumers and arbitrary dynamic access remain unknown, so renaming requires review."
            " Excluded files (including maintainer scripts), unselected packages and external consumers are unassessed, not proven absent; usage_scope records the configured analysis boundary."
        ),
        bad="class Worker:\n    def __init__(self):\n        self.pending = []\n    def run(self):\n        self.pending.clear()\n# tests/test_worker.py\nfrom app.worker import Worker\ndef test_run():\n    worker = Worker()\n    worker.run()\n    assert worker.pending == []",
        good="class Worker:\n    def __init__(self):\n        self._pending = []\n    def run(self):\n        self._pending.clear()\n        return 'done'\n# tests/test_worker.py\nfrom app.worker import Worker\ndef test_run():\n    assert Worker().run() == 'done'",
        fix="Review whether the attribute belongs to the supported API. For internal state, add a leading underscore, update class references and test observable public behavior. Keep deliberate external or framework contracts with a reasoned suppression. Suggested names avoid observed collisions; no automatic rename is performed.",
        config=(
            "rules",
            "tests.quality_exclude",
            "project.test_roots",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for attribute, references in ctx.member_usage.test_only_attributes(
            ctx.test_syntax
        ):
            qualified = f"{attribute.owner.name}.{attribute.name}"
            suggested_name = private_name(attribute.name, attribute.occupied_names)
            yield self.violation(
                f"{qualified} is public; among analyzed modules, only tests read it outside its defining class",
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
                    "test_references": [
                        {"path": path, "line": line} for path, line in references
                    ],
                    "automatic_rename": False,
                },
                hint=f"Before making {qualified} private as {suggested_name}, review excluded files (including maintainer scripts), unselected packages and external consumers: their uses were not assessed. Preserve deliberate public contracts; otherwise test public behavior.",
            )
