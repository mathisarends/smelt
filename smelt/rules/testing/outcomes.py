from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.test_outcomes import helper_outcome_review, vacuous_outcomes
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
        rationale=(
            "A test that merely calls production code can pass when its result is wrong. This opt-in syntactic check accepts assert (except literal assert True), pytest.raises/warns/deprecated_call, unittest.TestCase assertions, known Mock/AsyncMock interaction assertion methods, awaited typed Event.wait observations bounded by wait_for or timeout contexts that raise on expiry, and configured qualified helpers. A move_on_after scope or a configured timeout helper alone does not establish outcome evidence. Mock receivers come from recognized constructors, aliases, patch results or explicit fixture annotations, never arbitrary assert_* method names. It also follows one direct call to an unambiguous, undecorated, non-generator helper in the same file, including nested helpers and async helpers explicitly awaited or passed to asyncio.run. Uncalled definitions, unexecuted coroutines, imported helper bodies, fixture execution, callbacks and further helper calls are not inferred. Possibly swallowed helper assertions supply no evidence; known raising timeout contexts retain it. It neither proves assertions execute nor judges their strength; a frozen scheduler clock can also defeat a timeout. Positive validation/smoke tests can be intentional exceptions."
            " Checked inline Python subprocesses using an unshadowed sys.executable and literal or locally bound script text can provide direct top-level assert evidence. Dynamic scripts, files, optimized Python, shell commands and unchecked exits remain unknown; no analyzed code is executed."
        ),
        bad="def test_deactivate():\n    deactivate_user(1)",
        good="def test_deactivate():\n    user = deactivate_user(1)\n    assert user.status == 'inactive'",
        fix="Assert a concrete result or observable state, check the expected exception/warning, or configure a known assertion helper. Use a reasoned exception for deliberate smoke/interaction tests. Do not add a meaningless assertion to silence the finding. helper_review gives call locations and known reasons for missing evidence: imported bodies, unexecuted local coroutines, possible swallowing, or no recognized evidence within one hop. These explain an inference boundary, not proof that a helper contains no assertions.",
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
                    children = [
                        call
                        for call in test.calls
                        if call.qualified.startswith("subprocess.")
                    ]
                    yield self.violation(
                        f"{test.name} has no recognized outcome assertion",
                        path=module.path,
                        line=test.node.lineno,
                        expected={
                            "test": test.name,
                            "subprocess_review": [
                                {
                                    "line": call.node.lineno,
                                    "operation": call.qualified,
                                    "child_assertions": "not inferred",
                                }
                                for call in children
                            ],
                            "assertion_helpers": ctx.config.tests.assertion_helpers,
                            "helper_review": helper_outcome_review(
                                test, ctx.config.tests.assertion_helpers
                            ),
                            "analysis_limits": {
                                "helper_hops": 1,
                                "same_file_only": True,
                                "imported_bodies": "not analyzed",
                            },
                        },
                        hint="Check an observable result, state, expected exception/warning, or a configured assertion helper. Only one directly executed, eligible same-file helper is followed; imported bodies and further helper calls are not analyzed. helper_review explains missing evidence where known."
                        + (
                            " Child assertions were not inferred: only checked sys.executable -c calls with statically known script text and direct assertions are recognized. Assert the parent-visible outcome for dynamic scripts or files."
                            if children
                            else ""
                        ),
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
        rationale=(
            "Assertions confined to a result-dependent loop never execute if the result is empty. A positive all(...) assertion also accepts an empty collection. This opt-in review aid reports tests whose entire recognized evidence has one of these shapes, including assertions inside one directly executed same-file helper. Empty iteration inside a helper or around its call remains subject to review. Literal nonempty iteration without filters, explicit empty-result comparisons and tests with an additional independent outcome check are retained. It does not prove collection cardinality, reachability or test effectiveness, and can flag intentional universal properties over empty collections."
            " Checked inline Python subprocesses using an unshadowed sys.executable and literal or locally bound script text can provide direct top-level assert evidence. Dynamic scripts, files, optimized Python, shell commands and unchecked exits remain unknown; no analyzed code is executed."
        ),
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
