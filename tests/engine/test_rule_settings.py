from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError, parse_config
from smelt.diagnostics.debt import Debt
from smelt.diagnostics.violation import Severity
from smelt.engine.briefing import build_briefing, resolve_target
from smelt.engine.check import CheckOptions, snippet_reader
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path


def _project(tmp_path: Path, policy: str) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\n" + policy,
            "app/__init__.py": "",
            "app/api.py": "def helper(): return 1\n",
            "app/exported.py": "__all__ = ['supported']\ndef supported(): return 1\n",
            "app/legacy/__init__.py": "",
            "app/legacy/api.py": "def helper(): return 1\n",
            "app/legacy/new/__init__.py": "",
            "app/legacy/new/api.py": "def helper(): return 1\n",
        },
    )


def test_named_rule_needs_no_second_policy_and_preserves_explicit_api(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, "rules: {internal-public-member: warning}\n")

    found = check(root).report.violations

    assert {v.path for v in found} == {
        "app/api.py",
        "app/legacy/api.py",
        "app/legacy/new/api.py",
    }
    assert all(
        v.rule == "internal-public-member" and v.severity == Severity.WARNING
        for v in found
    )


def test_package_only_activation_and_closest_off_and_severity(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        """
structure:
  contracts:
    legacy:
      match: app.legacy
      rules: {internal-public-member: warning}
    new:
      match: app.legacy.new
      rules: {SMT316: error}
""",
    )

    found = check(root).report.violations

    assert [(v.path, v.severity) for v in found] == [
        ("app/legacy/api.py", Severity.WARNING),
        ("app/legacy/new/api.py", Severity.ERROR),
    ]
    with (root / "smelt.yaml").open("a", encoding="utf-8") as target:
        target.write(
            "    disabled:\n      match: app.legacy.new\n      rules: {internal-public-member: off}\n"
        )
    with pytest.raises(ConfigError, match="conflicting SMT316"):
        check(root)


def test_closest_package_override_can_reenable_a_globally_disabled_rule(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        """
rules: {internal-public-member: off}
structure:
  contracts:
    legacy:
      match: app.legacy
      rules: {internal-public-member: warning}
    new:
      match: app.legacy.new
      rules: {SMT316: off}
""",
    )

    outcome = check(root)

    assert [v.path for v in outcome.report.violations] == ["app/legacy/api.py"]
    briefing = build_briefing(
        outcome.context,
        resolve_target(outcome.context, "app/legacy/new/planned.py"),
        outcome.report,
    )
    assert briefing.to_json()["rules"]["internal-public-member"] == "off"
    assert (
        check(root, CheckOptions(ignore=("internal-public-member",))).report.violations
        == []
    )


@pytest.mark.parametrize("selector", ["SMT316", "smt316", "internal-public-member"])
def test_select_accepts_the_same_canonical_name_as_yaml(
    tmp_path: Path, selector: str
) -> None:
    root = _project(tmp_path, "")

    assert len(check(root, CheckOptions(select=(selector,))).report.violations) == 3
    assert check(root).report.violations == []


@pytest.mark.parametrize(
    "settings",
    [
        "SMT316: warning, internal-public-member: error",
        "internal-pubic-member: warning",
    ],
)
@pytest.mark.parametrize("scoped", [False, True])
def test_duplicate_aliases_and_typos_are_config_errors(
    tmp_path: Path, settings: str, scoped: bool
) -> None:
    policy = (
        f"structure:\n  contracts:\n    api:\n      match: app\n      rules: {{{settings}}}\n"
        if scoped
        else f"rules: {{{settings}}}\n"
    )
    with pytest.raises(ConfigError, match=r"already configured|unknown rule"):
        check(_project(tmp_path, policy))


def test_scoped_off_does_not_report_old_debt_as_fixed(tmp_path: Path) -> None:
    root = _project(tmp_path, "rules: {internal-public-member: warning}\n")
    original = check(root)
    Debt.from_violations(
        original.report.violations, snippet_reader(original.context)
    ).write(root / "debt.json")
    with (root / "smelt.yaml").open("a", encoding="utf-8") as target:
        target.write(
            "debt: debt.json\nstructure:\n  contracts:\n    legacy:\n      match: app.legacy\n      rules: {internal-public-member: off}\n"
        )

    outcome = check(root)

    assert outcome.report.violations == []
    assert outcome.resolved_debt == []


@pytest.mark.parametrize(
    "key",
    ["preset", "review_test_deadlines", "require_assertions", "member_visibility"],
)
def test_removed_switches_are_rejected_instead_of_silently_ignored(key: str) -> None:
    raw: dict[str, object] = {"version": 1, "project": {"root_packages": ["app"]}}
    if key == "preset":
        raw[key] = "maintainability"
    elif key == "member_visibility":
        raw["structure"] = {
            "contracts": {"api": {"match": "app", key: "private-when-internal"}}
        }
    else:
        raw["tests"] = {key: True}
    with pytest.raises(ConfigError, match=key):
        parse_config(raw)


@pytest.mark.parametrize(
    "name", ["unbounded-test-wait", "unused-suppression", "incomplete-coverage"]
)
def test_test_file_and_project_rules_cannot_silently_become_package_overrides(
    tmp_path: Path, name: str
) -> None:
    root = _project(
        tmp_path,
        f"structure:\n  contracts:\n    api:\n      match: app\n      rules: {{{name}: off}}\n",
    )

    with pytest.raises(ConfigError, match="configured at project level"):
        check(root)


def test_reasoned_ignore_accepts_the_canonical_rule_name(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        """
rules: {internal-public-member: warning}
ignore:
  - rule: internal-public-member
    modules: ['app.legacy.**']
    reason: Supported legacy callbacks used outside this repository.
""",
    )

    assert [v.path for v in check(root).report.violations] == ["app/api.py"]
