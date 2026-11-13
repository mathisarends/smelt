from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("SMT340",))
SOURCE = """\
async def read(browser):
    return ClipboardResult(text=await browser.clipboard.read())
"""


def _project(tmp_path: Path, source: str, *, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/channel.py": source},
    )


def _body(statement: str) -> str:
    return f"async def run(client, items, flag):\n    {statement}\n"


def test_keyword_argument_reports_location_and_suggests_keyword_name(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, SOURCE)
    [finding] = violations(root, OPTIONS)
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT340",
        "app/channel.py",
        2,
        33,
    )
    assert finding.expected == {
        "call": "ClipboardResult",
        "argument": "text",
        "awaited": "browser.clipboard.read()",
        "suggested_variable": "text",
        "automatic_rewrite": False,
    }
    assert finding.hint is not None
    assert "text = await browser.clipboard.read()" in finding.hint
    assert (root / "app/channel.py").read_text() == SOURCE


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
    [finding] = violations(_project(tmp_path, _body(statement)), OPTIONS)
    assert finding.expected is not None
    assert (finding.expected["call"], finding.expected["argument"]) == (call, argument)


def test_each_awaited_argument_is_reported_once(tmp_path: Path) -> None:
    source = _body("f(await client.a(), key=await client.b())")
    found = violations(_project(tmp_path, source), OPTIONS)
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
    assert violations(_project(tmp_path, _body(statement)), OPTIONS) == []


def test_while_body_is_still_reported(tmp_path: Path) -> None:
    source = _body("while flag:\n        f(await client.fetch())")
    [finding] = violations(_project(tmp_path, source), OPTIONS)
    assert finding.line == 3


def test_tests_are_not_checked(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "tests/test_channel.py": SOURCE,
        },
    )
    assert violations(root, OPTIONS) == []


def test_rule_is_opt_in_and_uses_configured_severity(tmp_path: Path) -> None:
    assert violations(_project(tmp_path / "default", SOURCE)) == []
    config = CONFIG + "rules: {await-in-call-argument: warning}\n"
    [finding] = violations(_project(tmp_path / "enabled", SOURCE, config=config))
    assert (finding.code, finding.severity) == ("SMT340", Severity.WARNING)
