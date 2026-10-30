from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.test_cases import independent_case_loops
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class IndependentTestCaseLoop(BaseRule):
    test_local = True
    scope = "test"
    code = "SMT427"
    name = "independent-test-case-loop"
    category = Category.TESTS
    default_severity = Severity.HINT
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A small literal loop repeats a candidate independent arrange/action/assert case.",
        rationale="A failure in the first independent case prevents later loop iterations from running and obscures case selection. This opt-in review aid considers top-level loops over 2-12 literal list/tuple cases with per-iteration construction/action and assertions. It excludes branches, context managers, control transfers, accumulated state, attribute writes and calls on pre-existing receivers. These exclusions preserve common replay, ownership-handoff and transaction/stream lifecycles. Independence cannot be proven syntactically; repeated calls alone and line counts are not smells.",
        bad="def test_select():\n    for path in ['app/a.py', 'app/b.py']:\n        selected = select(path)\n        assert selected == ['app']",
        good="@pytest.mark.parametrize('path', ['app/a.py', 'app/b.py'], ids=['a', 'b'])\ndef test_select(path):\n    assert select(path) == ['app']",
        fix="Review whether cases are independent, then use pytest.mark.parametrize with meaningful IDs. Retain loops when later actions depend on earlier state.",
        config=("rules", "tests.quality_exclude"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for module in ctx.test_syntax.modules.values():
            for test in module.tests:
                for loop in independent_case_loops(test):
                    yield self.violation(
                        f"{test.name} repeats candidate independent cases in a literal loop",
                        path=module.path,
                        line=loop.lineno,
                        expected={
                            "test": test.name,
                            "cases": len(loop.iter.elts)
                            if isinstance(loop.iter, ast.List | ast.Tuple)
                            else 0,
                        },
                        hint="Review parametrization with descriptive IDs; preserve stateful lifecycles.",
                    )
