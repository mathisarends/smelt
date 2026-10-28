from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class MissingTestAssertion(BaseRule):
    code = "SMT424"
    name = "missing-test-assertion"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A collected test has no recognized explicit outcome assertion.",
        rationale="A test that merely calls production code can pass when its result is wrong. This opt-in syntactic check accepts assert (except literal assert True), pytest.raises/warns/deprecated_call, unittest.TestCase assertion calls and configured qualified assertion helpers. Nested functions and fixture bodies do not supply evidence to their caller. It neither proves assertions execute nor judges their strength. Smoke tests and interaction-only boundary tests can be intentional exceptions.",
        bad="def test_deactivate():\n    deactivate_user(1)",
        good="def test_deactivate():\n    user = deactivate_user(1)\n    assert user.status == 'inactive'",
        fix="Assert a concrete result or observable state, check the expected exception/warning, or configure a known assertion helper. Use a reasoned exception for deliberate smoke/interaction tests. Do not add a meaningless assertion to silence the finding.",
        config=(
            "tests.require_assertions",
            "tests.assertion_helpers",
            "tests.quality_exclude",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        if not ctx.config.tests.require_assertions:
            return
        for module in ctx.test_syntax.modules.values():
            for test in module.tests:
                if not test.has_assertion(ctx.config.tests.assertion_helpers):
                    yield self.violation(
                        f"{test.name} has no recognized outcome assertion",
                        path=module.path,
                        line=test.node.lineno,
                        expected={
                            "test": test.name,
                            "require_assertions": True,
                            "assertion_helpers": ctx.config.tests.assertion_helpers,
                        },
                        hint="Check an observable result, state, expected exception/warning, or a configured assertion helper.",
                    )
