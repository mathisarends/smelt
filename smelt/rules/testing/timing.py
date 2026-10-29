from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.test_timing import unbounded_waits
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import PolicyActivation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class TestDeadline(BaseRule):
    activation = PolicyActivation(
        "tests.review_test_deadlines is enabled.",
        lambda config: config.tests.review_test_deadlines,
    )
    code = "SMT426"
    name = "test-deadline"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A known test wait has no local deadline, or the scheduler clock is replaced.",
        rationale="A wedged subprocess or known Event.wait can hang a suite. This opt-in review aid checks subprocess.run/call/check_call/check_output and typed Popen wait/communicate calls for their own timeout, and awaited asyncio Event/Task/Future or subprocess Process waits for enclosing wait_for or timeout contexts. Async processes come from awaited create_subprocess_exec/shell factories or explicit Process annotations, including known fixture returns. Each wait/communicate needs its own guard; an earlier bounded read or a preceding kill does not bound a later exit wait. Configured qualified timeout helpers can supply guards. Unknown/transitive waits, fixture/helper execution, external test-runner deadlines and general task flow are outside scope. Replacing the time method on a known running asyncio loop is reported separately because deadlines on that same clock can stop too. Controlled domain clocks, positive sleeps and sleep(0) yields are not prohibited.",
        bad="def test_import():\n    subprocess.run([sys.executable, '-c', 'import app'], check=True)",
        good="def test_import():\n    result = subprocess.run([sys.executable, '-c', 'import app'], capture_output=True, timeout=10)\n    assert result.returncode == 0, result.stderr",
        fix="Use a timeout at the blocking process boundary or around a known async wait. When replacing the scheduler clock, retain an independent real-time guard such as a parent-process timeout. Preserve bounded polling at real external boundaries.",
        config=(
            "tests.review_test_deadlines",
            "tests.timeout_helpers",
            "tests.quality_exclude",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        if not ctx.config.tests.review_test_deadlines:
            return
        for module in ctx.test_syntax.modules.values():
            for test in module.tests:
                for call in unbounded_waits(test, ctx.config.tests.timeout_helpers):
                    yield self.violation(
                        f"{test.name} has no recognized deadline for {call.qualified}",
                        path=module.path,
                        line=call.node.lineno,
                        expected={
                            "test": test.name,
                            "kind": "unbounded-wait",
                            "operation": call.qualified,
                            "provenance": call.provenance,
                        },
                        hint="Bound this operation locally or review the independently enforced test deadline.",
                    )
                for node in test.clock_replacements:
                    yield self.violation(
                        f"{test.name} replaces the asyncio scheduler/deadline clock",
                        path=module.path,
                        line=node.lineno,
                        expected={
                            "test": test.name,
                            "kind": "scheduler-clock",
                            "operation": "asyncio.AbstractEventLoop.time",
                        },
                        hint="Review an independent real-time guard; deadlines using this clock can also freeze.",
                    )
