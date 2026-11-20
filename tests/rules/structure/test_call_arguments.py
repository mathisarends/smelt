from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 2\nproject: {root_packages: [app]}\n"
AWAIT = CheckOptions(select=("SMT340",))
AWAIT_SOURCE = """\
async def read(browser):
    return ClipboardResult(text=await browser.clipboard.read())
"""


def _await_project(tmp_path: Path, source: str, *, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/channel.py": source},
    )


def _await_body(statement: str) -> str:
    return f"async def run(client, items, flag):\n    {statement}\n"


def test_keyword_argument_reports_location_and_suggests_keyword_name(
    tmp_path: Path,
) -> None:
    root = _await_project(tmp_path, AWAIT_SOURCE)
    [finding] = violations(root, AWAIT)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT340",
        "app/channel.py",
        2,
        33,
    )
    assert finding.expected == {
        "kind": "await",
        "call": "ClipboardResult",
        "argument": "text",
        "awaited": "browser.clipboard.read()",
        "suggested_variable": "text",
        "automatic_rewrite": False,
    }
    assert finding.hint is not None
    assert "text = await browser.clipboard.read()" in finding.hint
    assert (root / "app/channel.py").read_text() == AWAIT_SOURCE


@pytest.mark.parametrize(
    ("statement", "call", "argument"),
    [
        ("print(await client.fetch())", "print", None),
        ("client.store(1, await client.fetch())", "client.store", None),
        ("result = Wrapper(value=await client.fetch())", "Wrapper", "value"),
        ("await client.send(await client.fetch())", "client.send", None),
        ("async with client.open(await client.token()): pass", "client.open", None),
        ("if check(await client.fetch()): pass", "check", None),
        ("x = [1, f(await client.fetch())]", "f", None),
    ],
)
def test_direct_await_arguments_are_reported(
    tmp_path: Path, statement: str, call: str, argument: str | None
) -> None:
    [finding] = violations(_await_project(tmp_path, _await_body(statement)), AWAIT)
    assert finding.expected is not None
    assert (finding.expected["call"], finding.expected["argument"]) == (call, argument)


def test_each_awaited_argument_is_reported_once(tmp_path: Path) -> None:
    source = _await_body("f(await client.a(), key=await client.b())")
    found = violations(_await_project(tmp_path, source), AWAIT)
    assert [finding.expected["awaited"] for finding in found if finding.expected] == [
        "client.a()",
        "client.b()",
    ]


@pytest.mark.parametrize(
    "statement",
    [
        "return await client.fetch()",
        "value = await client.fetch()",
        "await client.fetch()",
        "await asyncio.gather(client.a(), client.b())",
        "f([await client.fetch()])",
        "f(await client.fetch() + 1)",
        "f(*await client.fetch())",
        "f(**await client.fetch())",
        "x = f(await client.fetch()) if flag else None",
        "x = flag and f(await client.fetch())",
        "x = [f(await client.get(i)) for i in items]",
        "while f(await client.poll()): pass",
    ],
)
def test_non_extractable_or_named_awaits_are_not_reported(
    tmp_path: Path, statement: str
) -> None:
    assert violations(_await_project(tmp_path, _await_body(statement)), AWAIT) == []


def test_while_body_is_still_reported(tmp_path: Path) -> None:
    source = _await_body("while flag:\n        f(await client.fetch())")
    [finding] = violations(_await_project(tmp_path, source), AWAIT)
    assert finding.line == 3


def test_awaits_in_tests_are_not_checked(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "tests/test_channel.py": AWAIT_SOURCE,
        },
    )
    assert violations(root, AWAIT) == []


def test_await_rule_is_opt_in_and_uses_configured_severity(tmp_path: Path) -> None:
    assert violations(_await_project(tmp_path / "default", AWAIT_SOURCE)) == []
    config = CONFIG + "rules: {complexity/inline-call-argument: warning}\n"
    [finding] = violations(
        _await_project(tmp_path / "enabled", AWAIT_SOURCE, config=config)
    )
    assert (finding.code, finding.severity) == ("SMT340", Severity.WARNING)


SELECT = CheckOptions(select=("SMT340",))
MAPPING_SOURCE = """\
def to_start_options_response(result: StartOptionsResult) -> StartOptionsResponse:
    return StartOptionsResponse(
        available_artificial_users=[
            to_artificial_user_response(artificial_user)
            for artificial_user in result.available_artificial_users
        ],
        previously_tested_websites=[
            PreviouslyTestedWebsite(url=website.url, favicon_url=website.favicon_url)
            for website in result.previously_tested_websites
        ],
        auth_profiles=[
            to_auth_profile_response(auth_profile)
            for auth_profile in result.auth_profiles
        ],
    )
"""


def test_issue_180_reports_each_transformation_without_rewriting(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, MAPPING_SOURCE)
    found = violations(root, CheckOptions(select=("SMT363", "SMT340")))
    assert [(v.code, v.line, v.column, v.end_line, v.end_column) for v in found] == [
        ("SMT340", 3, 36, 6, 10),
        ("SMT340", 7, 36, 10, 10),
        ("SMT340", 11, 23, 14, 10),
    ]
    assert found[0].severity == Severity.HINT
    assert found[0].expected == {
        "kind": "comprehension",
        "call": "StartOptionsResponse",
        "argument": "available_artificial_users",
        "comprehension_kind": "ListComp",
        "aggregate": None,
        "suggested_variable": "available_artificial_users",
        "automatic_rewrite": False,
    }
    assert found[0].hint is not None
    assert "available_artificial_users=available_artificial_users" in found[0].hint
    assert (root / "app/mapping.py").read_text() == MAPPING_SOURCE


@pytest.mark.parametrize(
    ("statement", "kind", "call", "argument"),
    [
        ("consume([convert(x) for x in items])", "ListComp", "consume", None),
        (
            "client.send(values={x.name for x in items})",
            "SetComp",
            "client.send",
            "values",
        ),
        (
            "return Response(data={x.id: x for x in items})",
            "DictComp",
            "Response",
            "data",
        ),
        ("consume(0, [x for x in items])", "ListComp", "consume", None),
        ("value = 0 < consume([x for x in items])", "ListComp", "consume", None),
        ("assert consume([x for x in items])", "ListComp", "consume", None),
        ("outer(inner(data=[x for x in items]))", "ListComp", "inner", "data"),
        (
            "await client.send([x async for x in items])",
            "ListComp",
            "client.send",
            None,
        ),
        ("consume([[y for y in x] for x in items])", "ListComp", "consume", None),
    ],
)
def test_eager_direct_arguments_are_reported(
    tmp_path: Path,
    statement: str,
    kind: str,
    *,
    call: str,
    argument: str | None,
) -> None:
    [finding] = violations(_project(tmp_path, _body(statement)), SELECT)
    assert finding.expected is not None
    assert finding.expected["comprehension_kind"] == kind
    assert finding.expected["call"] == call
    assert finding.expected["argument"] == argument
    assert finding.expected["suggested_variable"] == argument
    assert finding.hint is not None
    assert "preserve evaluation order" in finding.hint


MAPPER = """\
def to_usability_test(test: UsabilityTestData) -> UsabilityTest:
    return UsabilityTest(
        id=test.id,
        status=summarize_session_status(session.status for session in test.sessions),
        sessions=TestSessions(
            total=len(test.sessions),
            finished=sum(
                session.status in FINISHED_STATUSES for session in test.sessions
            ),
            items=[SessionReference(id=session.id) for session in test.sessions],
        ),
    )
"""


def test_builtin_reduction_is_named_while_direct_generators_stay_lazy(
    tmp_path: Path,
) -> None:
    found = violations(_project(tmp_path, MAPPER), SELECT)
    assert [(v.line, v.column, v.end_line, v.end_column) for v in found] == [
        (7, 22, 9, 14),
        (10, 19, 10, 81),
    ]
    assert found[0].expected == {
        "kind": "aggregate",
        "call": "TestSessions",
        "argument": "finished",
        "comprehension_kind": "GeneratorExp",
        "aggregate": "sum",
        "suggested_variable": "finished",
        "automatic_rewrite": False,
    }
    assert found[0].hint is not None
    assert "the sum() result to `finished`" in found[0].hint
    assert "finished=finished" in found[0].hint


@pytest.mark.parametrize(
    ("statement", "aggregate", "argument"),
    [
        ("consume(any(x.ready for x in items))", "any", None),
        ("consume(ready=all(x.ready for x in items))", "all", "ready"),
        ("consume(top=max((x.score for x in items), default=0))", "max", "top"),
        ("consume(low=min(x.score for x in items))", "min", "low"),
        ("consume(order=sorted((x for x in items), key=rank))", "sorted", "order"),
        ("consume(total=sum((x.size for x in items), 0))", "sum", "total"),
        ("await client.send(total=sum(x.size for x in items))", "sum", "total"),
    ],
)
def test_builtin_reductions_of_generators_are_reported(
    tmp_path: Path, statement: str, aggregate: str, argument: str | None
) -> None:
    [finding] = violations(_project(tmp_path, _body(statement)), SELECT)
    assert finding.expected is not None
    assert finding.expected["comprehension_kind"] == "GeneratorExp"
    assert finding.expected["aggregate"] == aggregate
    assert finding.expected["argument"] == argument


@pytest.mark.parametrize(
    "statement",
    [
        "total = sum(x.size for x in items)\n    consume(total=total)",
        "consume(total=sum(sizes))",
        "consume(total=summarize(x.size for x in items))",
        "consume(total=stats.sum(x.size for x in items))",
        "consume(total=sum(*(x.size for x in items)))",
        "consume(total=sum((x.size for x in items), **options))",
        "consume(total=sum(x.size async for x in items))",
        "value = consume(total=sum(x for x in items)) if flag else None",
        "value = [consume(total=sum(x for x in g)) for g in groups]",
    ],
)
def test_non_reduction_or_conditional_generators_are_excluded(
    tmp_path: Path, statement: str
) -> None:
    assert violations(_project(tmp_path, _body(statement)), SELECT) == []


@pytest.mark.parametrize(
    "source",
    [
        "from app.stats import sum\nconsume(total=sum(x for x in items))\n",
        "def sum(values):\n    return 0\n\n\nconsume(total=sum(x for x in items))\n",
        "def run(max):\n    consume(top=max(x for x in items))\n",
    ],
)
def test_shadowed_or_imported_reduction_names_are_excluded(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source), SELECT) == []


@pytest.mark.parametrize(
    "statement",
    [
        "values = [convert(x) for x in items]\n    consume(values)",
        "return [convert(x) for x in items]",
        "consume(result.users, convert(result))",
        "consume(convert(x) for x in items)",
        "consume(values=(convert(x) for x in items))",
        "consume(*[x for x in items])",
        "consume(**{x.name: x for x in items})",
        "consume(([x for x in items],))",
        "consume({'values': [x for x in items]})",
        "consume([x for x in items] + extra)",
        "value = consume([x for x in items]) if flag else None",
        "value = 1 if consume([x for x in items]) else 0",
        "value = flag and consume([x for x in items])",
        "value = flag or consume([x for x in items])",
        "value = lambda: consume([x for x in items])",
        "value = [consume([x for x in group]) for group in groups]",
        "value = {consume([x for x in group]) for group in groups}",
        "value = {key: consume([x for x in group]) for key, group in groups}",
        "value = (consume([x for x in group]) for group in groups)",
        "value = [x for x in consume([y for y in items])]",
        "while consume([x for x in items]): pass",
        "value = 0 < limit < consume([x for x in items])",
        "assert flag, consume([x for x in items])",
    ],
)
def test_named_lazy_nested_or_conditional_expressions_are_excluded(
    tmp_path: Path, statement: str
) -> None:
    assert violations(_project(tmp_path, _body(statement)), SELECT) == []


def test_guarded_and_loop_bodies_keep_their_local_extraction_point(
    tmp_path: Path,
) -> None:
    source = _body(
        "if flag:\n        consume([x for x in items])\n"
        "    while flag:\n        consume([x for x in items])\n"
        "    for group in groups:\n        consume([x for x in group])"
    )
    found = violations(_project(tmp_path, source), SELECT)
    assert [v.line for v in found] == [3, 5, 7]


def test_only_production_sources_are_checked(tmp_path: Path) -> None:
    root = _project(tmp_path, MAPPING_SOURCE)
    write_project(root, {"tests/test_mapping.py": MAPPING_SOURCE})
    found = violations(root, SELECT)
    assert len(found) == 3
    assert {v.path for v in found} == {"app/mapping.py"}


def test_rule_is_opt_in_and_configurable(tmp_path: Path) -> None:
    root = _project(tmp_path, "consume(values=[x for x in items])\n")
    assert violations(root) == []
    write_project(
        root,
        {"smelt.yml": CONFIG + "rules: {complexity/inline-call-argument: warning}\n"},
    )
    [finding] = violations(root)
    assert (finding.code, finding.severity) == ("SMT340", Severity.WARNING)


def test_package_overrides_suppression_and_uncached_results(tmp_path: Path) -> None:
    source = "consume(values=[x for x in items])\n"
    root = _project(tmp_path, source)
    write_project(
        root,
        {
            "smelt.yml": CONFIG
            + """\
rules: {complexity/inline-call-argument: hint}
style:
  overrides:
    quiet: {match: app.quiet, rules: {complexity/inline-call-argument: off}}
    strict: {match: app.strict, rules: {complexity/inline-call-argument: warning}}
""",
            "app/quiet/__init__.py": source,
            "app/strict/__init__.py": source,
            "app/intentional.py": source.rstrip()
            + "  # smelt: ignore[SMT340] -- preserve the existing argument evaluation order\n",
        },
    )
    found = violations(root)
    assert [(v.path, v.severity) for v in found] == [
        ("app/mapping.py", Severity.HINT),
        ("app/strict/__init__.py", Severity.WARNING),
    ]
    assert violations(root, CheckOptions(use_cache=False)) == found
    assert violations(root, SELECT) == found


def _project(tmp_path: Path, source: str) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "app/mapping.py": source},
    )


def _body(statement: str) -> str:
    return f"async def run(client, items, flag):\n    {statement}\n"


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [
        ("[await]", ["await"]),
        ("[comprehension]", ["comprehension"]),
        ("[aggregate]", ["aggregate"]),
        ("[await, aggregate]", ["await", "aggregate"]),
    ],
)
def test_call_argument_kinds_narrow_the_review(
    tmp_path: Path, kinds: str, expected: list[str]
) -> None:
    source = (
        "async def run(client, items):\n"
        "    consume(a=await client.fetch())\n"
        "    consume(b=[x for x in items])\n"
        "    consume(c=sum(x for x in items))\n"
    )
    root = _project(tmp_path, source)
    write_project(
        root,
        {"smelt.yml": CONFIG + f"style: {{call_argument_kinds: {kinds}}}\n"},
    )
    found = violations(root, SELECT)
    assert [(v.expected or {})["kind"] for v in found] == expected


def test_empty_call_argument_kinds_are_rejected(tmp_path: Path) -> None:
    root = _project(tmp_path, "consume(values=[x for x in items])\n")
    write_project(root, {"smelt.yml": CONFIG + "style: {call_argument_kinds: []}\n"})
    with pytest.raises(ConfigError, match="call_argument_kinds"):
        violations(root, SELECT)
