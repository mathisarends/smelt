from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.test_awaits import call_only_async_mocks
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import PolicyActivation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class CallOnlyAsyncMock(BaseRule):
    activation = PolicyActivation(
        "tests.review_async_mock_assertions is enabled.",
        lambda config: config.tests.review_async_mock_assertions,
    )
    code = "SMT428"
    name = "async-mock-call-only"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A known async mock is checked for calls without corresponding await evidence.",
        rationale="Calling an async mock can leave its coroutine unexecuted while call assertions still pass. This opt-in review checks direct known AsyncMock callables and explicitly async methods of known Mock/AsyncMock class specs, including aliases and consistent direct fixture returns. It accepts await assertions/history checks on the same mock and explicit negative call checks. Simple single-assignment locals can carry history values into assertions. Unknown specs, inherited or dynamically replaced methods, nested helpers and general data flow are not inferred. An AsyncMock object's arbitrary children are not assumed async. Deliberate creation/scheduling tests can assert_not_awaited or use a reasoned exception. This is evidence about an individual test, not proof that the suite misses a regression.",
        bad="def test_connect():\n    connect = AsyncMock()\n    run_handler(connect)\n    connect.assert_called_once_with('account')",
        good="def test_connect():\n    connect = AsyncMock()\n    run_handler(connect)\n    connect.assert_awaited_once_with('account')",
        fix="When execution matters, check await arguments/counts or assert_awaited_once_with on this async mock. Preserve intentional scheduling tests with an explicit expectation; keep synchronous interactions unchanged.",
        config=("tests.review_async_mock_assertions", "tests.quality_exclude"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        if not ctx.config.tests.review_async_mock_assertions:
            return
        for module in ctx.test_syntax.modules.values():
            for test in module.tests:
                for observation in call_only_async_mocks(test):
                    yield self.violation(
                        f"{test.name} checks calls to {observation.receiver} without await evidence",
                        path=module.path,
                        line=observation.node.lineno,
                        expected={
                            "test": test.name,
                            "receiver": observation.receiver,
                            "async_callable": observation.async_callable,
                            "provenance": observation.provenance,
                        },
                        hint="Check this operation was awaited when execution matters; review deliberate awaitable creation/scheduling.",
                    )
