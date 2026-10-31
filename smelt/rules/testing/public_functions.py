from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.member_names import private_name

if TYPE_CHECKING:
    from collections.abc import Iterator


class TestOnlyPublicFunction(BaseRule):
    code = "SMT429"
    name = "test-only-public-function"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.MEMBERS, Index.TESTS})
    doc = RuleDoc(
        summary="A public module function is accessed from tests but has no observed production use outside its own module.",
        rationale=(
            "Leaving an internal helper public just so tests can call it enlarges the apparent API and couples tests to implementation details. This opt-in review identifies undecorated top-level sync/async functions without a leading underscore, referenced by analyzed tests and not imported or accessed by another analyzed production module. Uses inside the defining module do not justify public visibility. Aliased/relative imports and analyzed re-exports are resolved; production imports, star imports and escaped module values conservatively preserve the API. Explicit __all__ exports, dynamic export declarations, decorated functions, rebound names, methods and nested functions are excluded. Test-quality exclusions apply to test evidence. External consumers, packaging entry points and arbitrary dynamic access are not fully known; a finding is a review candidate, not proof that a function can be made private safely."
            " Excluded files (including maintainer scripts), packages outside source selection and external consumers are unassessed, not proven absent. Findings expose the configured usage_scope."
        ),
        bad="__all__ = ['drag']\nasync def drag_gesture(tab): ...\nasync def drag(tab):\n    await drag_gesture(tab)\n# tests/test_mouse.py\nfrom app.mouse import drag_gesture",
        good="__all__ = ['drag']\nasync def _drag_gesture(tab): ...\nasync def drag(tab):\n    await _drag_gesture(tab)\n# tests/test_mouse.py\nfrom app.mouse import drag",
        fix="If this is an internal helper, add a leading underscore and update internal references. Prefer testing the public behavior that calls it. If direct helper tests are deliberate, keep a reasoned private-access exception; if it is a supported external API, declare it in __all__ or document a reasoned suppression. Review callers before renaming.",
        config=("rules", "tests.quality_exclude"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for function, references in ctx.member_usage.test_only_functions(
            ctx.test_syntax
        ):
            node = function.node
            suggested_name = private_name(node.name, function.occupied_names)
            yield self.violation(
                f"{function.module}.{node.name} is public; among analyzed modules, only tests access it outside its defining module",
                path=function.path,
                line=node.lineno,
                source_module=function.module,
                expected={
                    "function": node.name,
                    "suggested_name": suggested_name,
                    "usage_scope": ctx.member_usage.usage_scope,
                    "test_references": [
                        {"path": path, "line": line} for path, line in references
                    ],
                },
                hint=f"Before renaming {node.name} to {suggested_name}, review excluded files (including maintainer scripts), unselected packages and external consumers: their uses were not assessed. Preserve deliberate public APIs explicitly; otherwise test the public caller.",
            )
