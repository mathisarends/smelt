from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class MissingSkipReason(Rule):
    scope = "test"
    code = "TST013"
    name = "tests/missing-skip-reason"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A test is skipped, expected to fail or rerun without a tracked reason.",
        rationale=(
            "Switching a failing test off is quicker than fixing it, and without a "
            "trace of why, the skip stays long after its cause is gone. This opt-in "
            "review reports pytest.mark.skip, skipif, xfail and flaky markers, "
            "pytest.skip and pytest.xfail calls, unittest.skip, skipIf, skipUnless "
            "and expectedFailure, and the flaky and pytest-rerunfailures flaky "
            "decorators, wherever they appear in analyzed test modules: as "
            "decorators, pytestmark values, pytest.param marks or markers added in "
            "conftest.py hooks. A quarantine needs a reason that matches "
            "tests.skip_reason_pattern, a Rust regular expression that matches an "
            "issue reference or a link by default, "
            "in its reason argument or in a comment on its line or directly above. "
            "An environment limit only needs a non-empty reason: skipif and xfail "
            "with a runtime condition, a strict xfail, and skip or xfail calls "
            "inside an if branch or except handler. Literal conditions such as "
            "True are not an environment limit. A reason that is not a string "
            "literal is accepted because its text is unknown. pytest.importorskip "
            "is a dependency check and is not reported. Per-test timeouts belong to "
            "tests/unbounded-wait and are out of scope. Test-quality exclusions "
            "apply."
        ),
        bad=(
            "import pytest\n\n"
            "@pytest.mark.skip\n"
            "def test_sync(): ...\n\n"
            "@pytest.mark.flaky(reruns=3)\n"
            "def test_stream(): ..."
        ),
        good=(
            "import sys\n\nimport pytest\n\n"
            '@pytest.mark.skip(reason="#123: the sync API returns stale pages")\n'
            "def test_sync(): ...\n\n"
            '@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX signals")\n'
            "def test_signals(): ..."
        ),
        fix=(
            "Fix the test where possible. Otherwise give a quarantine a tracked "
            'reason such as reason="#123: <why>", or a comment that references '
            "the issue, and name the environment limit in the reason of a "
            "conditional skip. Set tests.skip_reason_pattern to your tracker's "
            "references, for example 'PROJ-\\d+'. No source is rewritten. Enable "
            "rules: {tests/missing-skip-reason: warning} or select TST013."
        ),
        canonical="Give every skip, xfail and rerun marker a reason that references an issue",
        config=("rules", "tests.skip_reason_pattern", "tests.quality_exclude"),
    )
