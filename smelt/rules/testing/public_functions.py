from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import PolicyActivation
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.member_names import private_name

if TYPE_CHECKING:
    from collections.abc import Iterator


class TestOnlyPublicFunction(BaseRule):
    activation = PolicyActivation(
        "tests.review_test_only_public_functions is enabled.",
        lambda config: config.tests.review_test_only_public_functions,
    )
    code = "SMT429"
    name = "test-only-public-function"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.MEMBERS, Index.TESTS})
    doc = RuleDoc(
        summary="A public module function is accessed from tests but has no observed production use outside its own module.",
        rationale="Leaving an internal helper public just so tests can call it enlarges the apparent API and couples tests to implementation details. This opt-in review identifies undecorated top-level sync/async functions without a leading underscore, referenced by analyzed tests and not imported or accessed by another analyzed production module. Uses inside the defining module do not justify public visibility. Aliased/relative imports and analyzed re-exports are resolved; production imports, star imports and escaped module values conservatively preserve the API. Explicit __all__ exports, dynamic export declarations, decorated functions, rebound names, methods and nested functions are excluded. Test-quality exclusions apply to test evidence. External consumers, packaging entry points and arbitrary dynamic access are not fully known; a finding is a review candidate, not proof that a function can be made private safely.",
        bad="__all__ = ['drag']\nasync def drag_gesture(tab): ...\nasync def drag(tab):\n    await drag_gesture(tab)\n# tests/test_mouse.py\nfrom app.mouse import drag_gesture",
        good="__all__ = ['drag']\nasync def _drag_gesture(tab): ...\nasync def drag(tab):\n    await _drag_gesture(tab)\n# tests/test_mouse.py\nfrom app.mouse import drag",
        fix="If this is an internal helper, add a leading underscore and update internal references. Prefer testing the public behavior that calls it. If direct helper tests are deliberate, keep a reasoned private-access exception; if it is a supported external API, declare it in __all__ or document a reasoned suppression. Review callers before renaming.",
        config=("tests.review_test_only_public_functions", "tests.quality_exclude"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        if not ctx.config.tests.review_test_only_public_functions:
            return
        for function, references in ctx.member_usage.test_only_functions(
            ctx.test_syntax
        ):
            node = function.node
            suggested_name = private_name(node.name, function.occupied_names)
            yield self.violation(
                f"{function.module}.{node.name} is public, but only tests access it outside its defining module",
                path=function.path,
                line=node.lineno,
                source_module=function.module,
                expected={
                    "function": node.name,
                    "suggested_name": suggested_name,
                    "test_references": [
                        {"path": path, "line": line} for path, line in references
                    ],
                },
                hint=f"Review renaming {node.name} to {suggested_name} and test its public caller; preserve deliberate external APIs explicitly.",
            )
