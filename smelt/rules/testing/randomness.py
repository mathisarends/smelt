from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

_GENERATORS = frozenset(
    {
        "uuid.uuid1",
        "uuid.uuid4",
        "os.urandom",
        "secrets.choice",
        "secrets.randbelow",
        "secrets.randbits",
        "secrets.token_bytes",
        "secrets.token_hex",
        "secrets.token_urlsafe",
        *(
            f"random.{name}"
            for name in (
                "random",
                "randint",
                "randrange",
                "choice",
                "choices",
                "sample",
                "shuffle",
                "uniform",
                "getrandbits",
                "randbytes",
            )
        ),
    }
)


class NondeterministicTestData(Rule):
    test_local = True
    scope = "test"
    code = "SMT431"
    name = "nondeterministic-test-data"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A test module calls a known random or time-derived data generator.",
        rationale="Fixed IDs and values keep ordinary test setup reproducible and make failures easier to replay. This opt-in review recognizes direct uuid1/uuid4, os.urandom, secrets and common module-level random calls, including resolved import aliases and fixture/helper bodies in analyzed test modules. It groups calls on one source line. It does not infer whether randomness is the behavior under test. Seeded Random instances and unknown generators are outside scope; module-level random calls are reviewed even if a seed was set elsewhere. Production source and test-quality exclusions are not checked.",
        bad="from uuid import uuid4\n\ndef test_create():\n    lease = create(uuid4(), uuid4())\n    assert lease.active",
        good="from uuid import UUID\n\ndef test_create():\n    lease = create(UUID(int=1), UUID(int=2))\n    assert lease.active",
        fix="Use fixed inputs such as UUID(int=1), or an explicitly seeded local random.Random(seed) when generated variation matters. Keep tests of randomness, entropy and unique-ID behavior with a reasoned suppression. Preserve falsy-ID regression scenarios; change only incidental random setup. No source is rewritten. Enable rules: {nondeterministic-test-data: warning} or select SMT431.",
        config=("rules", "tests.quality_exclude"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for module in ctx.test_syntax.modules.values():
            calls: dict[int, set[str]] = defaultdict(set)
            for call in module.calls:
                if call.qualified in _GENERATORS:
                    calls[call.node.lineno].add(call.qualified)
            for line, generators in sorted(calls.items()):
                yield self.violation(
                    "review random test data; prefer fixed inputs or a seeded local generator",
                    path=module.path,
                    line=line,
                    expected={
                        "generators": sorted(generators),
                        "automatic_rewrite": False,
                    },
                    hint="Use fixed IDs/values for incidental setup; keep intentional randomness tests with a specific reason.",
                )
