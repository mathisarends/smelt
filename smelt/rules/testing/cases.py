from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class IndependentTestCaseLoop(Rule):
    scope = "test"
    code = "TST009"
    name = "tests/independent-case-loop"
    category = Category.TESTS
    default_severity = Severity.HINT
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A small literal loop repeats a candidate independent arrange/action/assert case.",
        rationale="A failure in the first independent case prevents later loop iterations from running and obscures case selection. This opt-in review aid considers top-level loops over 2-12 literal list/tuple cases with per-iteration construction/action and assertions. It excludes branches, context managers, control transfers, accumulated state, attribute writes and calls on pre-existing receivers. These exclusions preserve common replay, ownership-handoff and transaction/stream lifecycles. Independence cannot be proven syntactically; repeated calls alone and line counts are not smells.",
        bad="def test_select():\n    for path in ['app/a.py', 'app/b.py']:\n        selected = select(path)\n        assert selected == ['app']",
        good="@pytest.mark.parametrize('path', ['app/a.py', 'app/b.py'], ids=['a', 'b'])\ndef test_select(path):\n    assert select(path) == ['app']",
        fix="Review whether cases are independent, then use pytest.mark.parametrize with meaningful IDs. Retain loops when later actions depend on earlier state.",
        canonical=(
            "Use pytest.mark.parametrize instead of looping over independent cases"
        ),
        config=("rules", "tests.quality_exclude"),
    )
