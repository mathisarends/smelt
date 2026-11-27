from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.debt import Debt
from smelt.engine.check import CheckOptions, snippet_reader
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.diagnostics.violation import Violation

OPTIONS = CheckOptions(select=("IDM002",))
CONFIG = """version: 2
project: {root_packages: [app], python_version: '3.12'}
style:
  overrides:
    dispatch: {match: app, type_dispatch: {style: match, min_branches: 3}}
"""
SOURCE = "if isinstance(event, Started): start(event)\nelif isinstance(event, Progress): update(event)\nelif isinstance(event, Finished): finish(event)\n"


def _project(tmp_path: Path, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path, {"smelt.yml": config, "app/__init__.py": "", "app/events.py": SOURCE}
    )


def test_type_dispatch_reports_a_structured_review_hint(tmp_path: Path) -> None:
    root = _project(tmp_path)
    before = (root / "app/events.py").read_bytes()
    [finding] = violations(root, OPTIONS)
    assert finding.severity.value == "hint"
    assert finding.line == 1
    assert finding.expected is not None
    assert finding.expected["subject"] == "event"
    assert finding.expected["automatic_rewrite"] is False
    assert len(finding.expected["branches"]) == 3
    assert (root / "app/events.py").read_bytes() == before


@pytest.mark.parametrize(
    "config",
    [
        "version: 2\nproject: {root_packages: [app]}",
        CONFIG.replace("style: match", "style: allow"),
        CONFIG.replace("min_branches: 3", "min_branches: 4"),
        CONFIG.replace("'3.12'", "'3.9'"),
    ],
)
def test_policy_threshold_and_python_minimum_are_respected(
    tmp_path: Path, config: str
) -> None:
    assert violations(_project(tmp_path, config), OPTIONS) == []


def test_unknown_python_minimum_keeps_version_review_in_the_evidence(
    tmp_path: Path,
) -> None:
    [finding] = violations(
        _project(tmp_path, CONFIG.replace(", python_version: '3.12'", "")), OPTIONS
    )
    assert finding.expected is not None
    assert finding.expected["python"]["minimum"] is None


def test_closest_allow_override_wins(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        CONFIG + "    exception: {match: app.compat, type_dispatch: {style: allow}}\n",
    )
    write_project(
        root,
        {
            "app/compat/__init__.py": "",
            "app/compat/events.py": SOURCE,
            "app/events.py": "",
        },
    )
    assert violations(root, OPTIONS) == []
    policy = check(root, OPTIONS).context.contracts.dispatch_brief("app.compat.events")
    assert policy is not None
    assert policy["style"] == "allow"


def test_suppression_and_severity_overrides_are_supported(tmp_path: Path) -> None:
    root = _project(tmp_path, CONFIG + "rules: {IDM002: error}\n")
    source = root / "app/events.py"
    source.write_text(
        SOURCE.replace(
            ": start(event)",
            ": start(event)  # smelt: ignore[IDM002] -- retain current dispatch",
        )
    )
    outcome = check(root, OPTIONS)
    assert outcome.report.violations == []
    assert outcome.report.suppressed == 1
    source.write_text(SOURCE)
    assert check(root, OPTIONS).report.failed


def test_an_added_dispatch_branch_is_not_hidden_by_existing_debt(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    initial = check(root, OPTIONS)
    debt = Debt.from_violations(
        initial.report.violations, snippet_reader(initial.context)
    )
    source = root / "app/events.py"
    source.write_text(SOURCE + "elif isinstance(event, Cancelled): cancel(event)\n")
    after = check(root, OPTIONS)
    new, known, _ = debt.match(after.report.violations, snippet_reader(after.context))
    assert len(new) == 1
    assert len(known) == 1
    assert known[0].expected is not None
    assert len(known[0].expected["dispatch_edges"]) == 3


GUARDS = """def convert(event):
    if isinstance(event, Started):
        return start(event)
    if isinstance(event, Delta | Progress) and event.ok:
        return update(event)
    if isinstance(event, Finished):
        raise Done(event)
"""


def _guards(tmp_path: Path, source: str) -> list[Violation]:
    return violations(
        write_project(
            tmp_path,
            {"smelt.yml": CONFIG, "app/__init__.py": "", "app/events.py": source},
        ),
        OPTIONS,
    )


def test_exiting_guards_on_one_subject_form_a_chain(tmp_path: Path) -> None:
    [finding] = _guards(tmp_path, GUARDS)
    assert finding.line == 2
    assert "guards" in finding.message
    assert finding.hint is not None
    assert "case _" in finding.hint
    assert finding.expected is not None
    assert finding.expected["form"] == "guard"
    assert finding.expected["fallback"] is None
    assert [branch["line"] for branch in finding.expected["branches"]] == [2, 4, 6]
    assert finding.expected["branches"][1]["guard"] == "event.ok"


def test_raise_terminated_guards_and_module_level_runs_are_reported(
    tmp_path: Path,
) -> None:
    source = (
        "if isinstance(value, int):\n    raise A\n"
        "if isinstance(value, str):\n    raise B\n"
        "if isinstance(value, bytes):\n    raise C\n"
    )
    [finding] = _guards(tmp_path, source)
    assert finding.expected is not None
    assert finding.expected["scope"] == "module"


def test_a_trailing_return_or_raise_is_recorded_as_the_fallback(
    tmp_path: Path,
) -> None:
    [finding] = _guards(tmp_path, GUARDS + "    raise UnknownEvent(event)\n")
    assert finding.expected is not None
    assert finding.expected["fallback"] == 8
    assert len(finding.expected["branches"]) == 3


def test_an_elif_chain_reports_its_form(tmp_path: Path) -> None:
    [finding] = violations(_project(tmp_path), OPTIONS)
    assert finding.expected is not None
    assert finding.expected["form"] == "elif"


@pytest.mark.parametrize(
    "source",
    [
        GUARDS.replace("        return update(event)\n", "        update(event)\n"),
        GUARDS.replace(
            "        return update(event)\n",
            "        if event.ok:\n            return update(event)\n",
        ),
        GUARDS.replace(
            "    if isinstance(event, Delta",
            "    log(event)\n    if isinstance(event, Delta",
        ),
        GUARDS.replace("isinstance(event, Delta", "isinstance(other, Delta"),
        GUARDS.replace("if isinstance(event, Delta", "if not isinstance(event, Delta"),
        GUARDS.replace(
            "        return update(event)\n",
            "        return update(event)\n    else:\n        pass\n",
        ),
        "def convert(events):\n    for event in events:\n"
        "        if isinstance(event, A):\n            continue\n"
        "        if isinstance(event, B):\n            continue\n"
        "        if isinstance(event, C):\n            break\n",
    ],
    ids=[
        "fallthrough",
        "nested-exit",
        "interleaved",
        "other-subject",
        "negative",
        "else",
        "loop-exits",
    ],
)
def test_guards_that_are_not_an_elif_chain_stay_quiet(
    tmp_path: Path, source: str
) -> None:
    assert _guards(tmp_path, source) == []


def test_a_broken_run_restarts_with_the_next_guard(tmp_path: Path) -> None:
    source = GUARDS + (
        "    log(event)\n"
        "    if isinstance(event, A):\n        return 1\n"
        "    if isinstance(event, B):\n        return 2\n"
        "    if isinstance(event, C):\n        return 3\n"
    )
    findings = _guards(tmp_path, source)
    assert [finding.line for finding in findings] == [2, 9]
