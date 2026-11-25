from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class NondeterministicTestData(Rule):
    scope = "test"
    code = "TST011"
    name = "tests/nondeterministic-data"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary=(
            "A test module calls a known random or time-derived data generator, "
            "or reads the wall clock."
        ),
        rationale=(
            "Fixed IDs, values and instants keep ordinary test setup reproducible "
            "and make failures easier to replay. Tests that read the real clock also "
            "fail around midnight, month ends and DST changes. This opt-in review "
            "recognizes direct uuid1/uuid4, os.urandom, secrets and common "
            "module-level random calls, and the wall-clock reads datetime.now, "
            "datetime.utcnow, datetime.today, date.today, time.time and "
            "time.time_ns, including resolved import aliases and fixture/helper "
            "bodies in analyzed test modules. It groups calls on one source line. "
            "It does not infer whether randomness or the clock is the behavior under "
            "test. Clock reads inside a freezegun.freeze_time or time_machine.travel "
            "with-block, in a function or class those decorate or marked "
            "pytest.mark.freeze_time, and in tests that request pytest-freezer's "
            "freezer fixture are not reported; a helper called from such a test is "
            "still reviewed. time.monotonic and time.perf_counter measure real "
            "deadlines and are out of scope. Seeded Random instances and unknown "
            "generators are outside scope; module-level random calls are reviewed "
            "even if a seed was set elsewhere. Production source and test-quality "
            "exclusions are not checked."
        ),
        bad=(
            "from datetime import datetime, timedelta\n\n"
            "def test_expired():\n"
            "    lease = Lease(expires_at=datetime.now() - timedelta(seconds=1))\n"
            "    assert lease.expired(now=datetime.now())"
        ),
        good=(
            "from datetime import UTC, datetime, timedelta\n\n"
            "NOW = datetime(2026, 1, 1, tzinfo=UTC)\n\n"
            "def test_expired():\n"
            "    lease = Lease(expires_at=NOW - timedelta(seconds=1))\n"
            "    assert lease.expired(now=NOW)"
        ),
        fix=(
            "Use fixed inputs such as UUID(int=1), or an explicitly seeded local "
            "random.Random(seed) when generated variation matters. Pass a fixed "
            "instant such as datetime(2026, 1, 1, tzinfo=UTC) or an injected clock "
            "or now parameter instead of reading the clock; a freeze_time context "
            "also works. Keep tests of randomness, entropy, unique-ID behavior and "
            "clock adapters with a reasoned suppression. Preserve falsy-ID "
            "regression scenarios; change only incidental random setup. No source "
            "is rewritten. Enable rules: {tests/nondeterministic-data: warning} or "
            "select TST011."
        ),
        canonical=(
            "Use fixed test data such as UUID(int=1) and fixed instants, "
            "not random values or the wall clock"
        ),
        config=("rules", "tests.quality_exclude"),
    )
