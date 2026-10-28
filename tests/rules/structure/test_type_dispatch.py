from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.debt import Debt
from smelt.engine.check import CheckOptions, snippet_reader
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

OPTIONS = CheckOptions(select=("SMT315",))
CONFIG = """version: 1
project: {root_packages: [app], python_version: '3.12'}
structure:
  contracts:
    dispatch: {match: app, type_dispatch: {style: match, min_branches: 3}}
"""
SOURCE = "if isinstance(event, Started): start(event)\nelif isinstance(event, Progress): update(event)\nelif isinstance(event, Finished): finish(event)\n"


def _project(tmp_path: Path, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path, {"smelt.yaml": config, "app/__init__.py": "", "app/events.py": SOURCE}
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
        "version: 1\nproject: {root_packages: [app]}",
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
    root = _project(tmp_path, CONFIG + "rules: {SMT315: error}\n")
    source = root / "app/events.py"
    source.write_text(
        SOURCE.replace(
            ": start(event)",
            ": start(event)  # smelt: ignore[SMT315] -- retain current dispatch",
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
