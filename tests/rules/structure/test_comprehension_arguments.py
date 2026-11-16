from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT366",))
SOURCE = """\
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
    root = _project(tmp_path, SOURCE)
    found = violations(root, CheckOptions(select=("SMT363", "SMT366")))
    assert [(v.code, v.line, v.column, v.end_line, v.end_column) for v in found] == [
        ("SMT366", 3, 36, 6, 10),
        ("SMT366", 7, 36, 10, 10),
        ("SMT366", 11, 23, 14, 10),
    ]
    assert found[0].severity == Severity.HINT
    assert found[0].expected == {
        "call": "StartOptionsResponse",
        "argument": "available_artificial_users",
        "comprehension_kind": "ListComp",
        "suggested_variable": "available_artificial_users",
        "automatic_rewrite": False,
    }
    assert found[0].hint is not None
    assert "available_artificial_users=available_artificial_users" in found[0].hint
    assert (root / "app/mapping.py").read_text() == SOURCE


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
    root = _project(tmp_path, SOURCE)
    write_project(root, {"tests/test_mapping.py": SOURCE})
    found = violations(root, SELECT)
    assert len(found) == 3
    assert {v.path for v in found} == {"app/mapping.py"}


def test_rule_is_opt_in_and_configurable(tmp_path: Path) -> None:
    root = _project(tmp_path, "consume(values=[x for x in items])\n")
    assert violations(root) == []
    write_project(
        root,
        {"smelt.yml": CONFIG + "rules: {comprehension-in-call-argument: warning}\n"},
    )
    [finding] = violations(root)
    assert (finding.code, finding.severity) == ("SMT366", Severity.WARNING)


def test_package_overrides_suppression_and_uncached_results(tmp_path: Path) -> None:
    source = "consume(values=[x for x in items])\n"
    root = _project(tmp_path, source)
    write_project(
        root,
        {
            "smelt.yml": CONFIG
            + """\
rules: {comprehension-in-call-argument: hint}
structure:
  contracts:
    quiet: {match: app.quiet, rules: {comprehension-in-call-argument: off}}
    strict: {match: app.strict, rules: {comprehension-in-call-argument: warning}}
""",
            "app/quiet/__init__.py": source,
            "app/strict/__init__.py": source,
            "app/intentional.py": source.rstrip()
            + "  # smelt: ignore[SMT366] -- preserve the existing argument evaluation order\n",
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
