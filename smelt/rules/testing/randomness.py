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
        summary="A test module calls a known random or time-derived data generator.",
        rationale="Fixed IDs and values keep ordinary test setup reproducible and make failures easier to replay. This opt-in review recognizes direct uuid1/uuid4, os.urandom, secrets and common module-level random calls, including resolved import aliases and fixture/helper bodies in analyzed test modules. It groups calls on one source line. It does not infer whether randomness is the behavior under test. Seeded Random instances and unknown generators are outside scope; module-level random calls are reviewed even if a seed was set elsewhere. Production source and test-quality exclusions are not checked.",
        bad="from uuid import uuid4\n\ndef test_create():\n    lease = create(uuid4(), uuid4())\n    assert lease.active",
        good="from uuid import UUID\n\ndef test_create():\n    lease = create(UUID(int=1), UUID(int=2))\n    assert lease.active",
        fix="Use fixed inputs such as UUID(int=1), or an explicitly seeded local random.Random(seed) when generated variation matters. Keep tests of randomness, entropy and unique-ID behavior with a reasoned suppression. Preserve falsy-ID regression scenarios; change only incidental random setup. No source is rewritten. Enable rules: {tests/nondeterministic-data: warning} or select TST011.",
        canonical=(
            "Use fixed test data such as UUID(int=1), not random or time-derived values"
        ),
        config=("rules", "tests.quality_exclude"),
    )
