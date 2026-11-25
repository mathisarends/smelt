from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class AsyncMockWithoutAwaitCheck(Rule):
    scope = "test"
    code = "TST010"
    name = "tests/missing-await-assertion"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A known async mock is checked for calls without corresponding await evidence.",
        rationale="Calling an async mock can leave its coroutine unexecuted while call assertions still pass. This opt-in review checks direct known AsyncMock callables and explicitly async methods of known Mock/AsyncMock class specs, including aliases and consistent direct fixture returns. It accepts await assertions/history checks on the same mock and explicit negative call checks. Simple single-assignment locals can carry history values into assertions. Unknown specs, inherited or dynamically replaced methods, nested helpers and general data flow are not inferred. An AsyncMock object's arbitrary children are not assumed async. Deliberate creation/scheduling tests can assert_not_awaited or use a reasoned exception. This is evidence about an individual test, not proof that the suite misses a regression.",
        bad="def test_connect():\n    connect = AsyncMock()\n    run_handler(connect)\n    connect.assert_called_once_with('account')",
        good="def test_connect():\n    connect = AsyncMock()\n    run_handler(connect)\n    connect.assert_awaited_once_with('account')",
        fix="When execution matters, check await arguments/counts or assert_awaited_once_with on this async mock. Preserve intentional scheduling tests with an explicit expectation; keep synchronous interactions unchanged.",
        canonical="Check async mocks with assert_awaited*, not only assert_called*",
        config=("rules", "tests.quality_exclude"),
    )
