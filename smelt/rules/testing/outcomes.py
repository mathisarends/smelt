from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.test_outcomes import vacuous_outcomes
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class TestWithoutOutcomeCheck(BaseRule):
    test_local = True
    scope = "test"
    code = "SMT424"
    name = "test-without-outcome-check"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A collected test has no recognized explicit outcome assertion.",
        rationale="A test that merely calls production code can pass when its result is wrong. This opt-in syntactic check accepts assert (except literal assert True), pytest.raises/warns/deprecated_call, unittest.TestCase assertions, known Mock/AsyncMock interaction assertion methods, awaited typed Event.wait observations bounded by wait_for or timeout contexts that raise on expiry, and configured qualified helpers. A move_on_after scope or a configured timeout helper alone does not establish outcome evidence. Mock receivers come from recognized constructors, aliases, patch results or explicit fixture annotations, never arbitrary assert_* method names. Nested functions and fixture bodies do not supply evidence to callers. It neither proves assertions execute nor judges their strength; a frozen scheduler clock can also defeat a timeout. Positive validation/smoke tests can be intentional exceptions.",
        bad="def test_deactivate():\n    deactivate_user(1)",
        good="def test_deactivate():\n    user = deactivate_user(1)\n    assert user.status == 'inactive'",
        fix="Assert a concrete result or observable state, check the expected exception/warning, or configure a known assertion helper. Use a reasoned exception for deliberate smoke/interaction tests. Do not add a meaningless assertion to silence the finding.",
        config=(
            "rules",
            "tests.assertion_helpers",
            "tests.quality_exclude",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for module in ctx.test_syntax.modules.values():
            for test in module.tests:
                if not test.has_assertion(ctx.config.tests.assertion_helpers):
                    yield self.violation(
                        f"{test.name} has no recognized outcome assertion",
                        path=module.path,
                        line=test.node.lineno,
                        expected={
                            "test": test.name,
                            "assertion_helpers": ctx.config.tests.assertion_helpers,
                        },
                        hint="Check an observable result, state, expected exception/warning, or a configured assertion helper.",
                    )


class TestOnlyChecksPossiblyEmptyData(BaseRule):
    test_local = True
    scope = "test"
    code = "SMT425"
    name = "test-only-checks-possibly-empty-data"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="Every recognized outcome can disappear or pass trivially on empty input.",
        rationale="Assertions confined to a result-dependent loop never execute if the result is empty. A positive all(...) assertion also accepts an empty collection. This opt-in review aid reports tests whose entire recognized evidence has one of these shapes. Literal nonempty iteration without filters, explicit empty-result comparisons and tests with an additional independent outcome check are retained. It does not prove collection cardinality, reachability or test effectiveness, and can flag intentional universal properties over empty collections.",
        bad="def test_codegen():\n    for name, content in generate().items():\n        assert committed[name] == content",
        good="def test_codegen():\n    assert generate() == independently_loaded_committed_files()",
        fix="Compare the complete result with an independent expectation, or check expected presence/cardinality before universal checks. Keep intentional empty-result cases explicit; do not add cosmetic assertions.",
        config=(
            "rules",
            "tests.assertion_helpers",
            "tests.quality_exclude",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for module in ctx.test_syntax.modules.values():
            for test in module.tests:
                risks = vacuous_outcomes(test, ctx.config.tests.assertion_helpers)
                if risks:
                    yield self.violation(
                        f"{test.name} only checks possibly empty data; its assertions can pass or be skipped without checking an item",
                        path=module.path,
                        line=test.node.lineno,
                        expected={
                            "test": test.name,
                            "evidence": [
                                {
                                    "line": node.lineno,
                                    "kind": "loop"
                                    if isinstance(node, ast.For | ast.AsyncFor)
                                    else "all",
                                }
                                for node in risks
                            ],
                        },
                        hint="Compare complete output or verify expected presence before iterating; review deliberate empty-result properties.",
                    )
