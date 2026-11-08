from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError, parse_config
from smelt.diagnostics.debt import Debt
from smelt.diagnostics.violation import Severity
from smelt.engine.briefing import build_briefing, resolve_target
from smelt.engine.check import CheckOptions, snippet_reader
from smelt.engine.schema import config_json_schema
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


def _project(tmp_path: Path, policy: str) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\n" + policy,
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
    with (root / "smelt.yml").open("a", encoding="utf-8") as target:
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
    with (root / "smelt.yml").open("a", encoding="utf-8") as target:
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


_GROUP_CODES = {"SMT316", "SMT320", "SMT335", "SMT429", "SMT430"}
_GROUP_SOURCE = """LIMIT = 3
def helper(): return LIMIT
def testing_hook(): return 0
class Worker:
    def __init__(self):
        self.pending = 0
        self.debug = 0
    def run(self): return helper() + self.pending
"""
_GROUP_TEST = """from app.api import Worker, testing_hook
def test_api():
    worker = Worker()
    assert worker.run() == 3
    assert worker.debug == 0
    assert testing_hook() == 0
"""


def _group_project(tmp_path: Path, policy: str = "information-hiding: warning") -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": f"version: 1\nproject: {{root_packages: [app]}}\nrules: {{{policy}}}\n",
            "app/__init__.py": "",
            "app/api.py": _GROUP_SOURCE,
            "tests/test_api.py": _GROUP_TEST,
        },
    )


def test_information_hiding_includes_test_only_apis_without_duplicates(
    tmp_path: Path,
) -> None:
    root = _group_project(tmp_path)
    outcome = check(root)
    findings = outcome.report.violations
    assert {v.code for v in findings} == _GROUP_CODES
    assert len(findings) == 5
    assert all(v.severity == Severity.WARNING for v in findings)
    assert all(
        meta.groups == ("information-hiding",)
        for meta in outcome.report.rules
        if meta.code in _GROUP_CODES
    )
    assert findings == violations(root, CheckOptions(use_cache=False))
    assert not {"SMT317", "SMT334"} & {rule.code for rule, _ in outcome.active}


def test_existing_individual_configuration_retains_identical_findings(
    tmp_path: Path,
) -> None:
    root = _group_project(tmp_path)
    grouped = violations(root)
    _group_project(
        tmp_path, ", ".join(f"{code}: warning" for code in sorted(_GROUP_CODES))
    )
    assert violations(root) == grouped


@pytest.mark.parametrize(
    "policy",
    [
        "information-hiding: warning, SMT335: off",
        "SMT335: off, information-hiding: warning",
    ],
)
def test_specific_rule_overrides_group_regardless_of_key_order(
    tmp_path: Path, policy: str
) -> None:
    findings = violations(_group_project(tmp_path, policy))
    assert {v.code for v in findings} == _GROUP_CODES - {"SMT335"}


@pytest.mark.parametrize(
    "policy",
    [
        "information-hiding: off, SMT335: error",
        "SMT335: error, information-hiding: off",
    ],
)
def test_group_can_be_disabled_with_one_individual_exception(
    tmp_path: Path, policy: str
) -> None:
    [finding] = violations(_group_project(tmp_path, policy))
    assert (finding.code, finding.severity) == ("SMT335", Severity.ERROR)


def test_individual_severity_override(tmp_path: Path) -> None:
    findings = violations(
        _group_project(
            tmp_path, "information-hiding: warning, internal-public-attribute: hint"
        )
    )
    assert {v.code for v in findings} == _GROUP_CODES
    assert {v.severity for v in findings if v.code == "SMT335"} == {Severity.HINT}
    assert {v.severity for v in findings if v.code != "SMT335"} == {Severity.WARNING}


def test_group_does_not_allow_duplicate_individual_aliases(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="SMT335 is already configured"):
        check(
            _group_project(
                tmp_path,
                "information-hiding: warning, SMT335: off, internal-public-attribute: warning",
            )
        )


@pytest.mark.parametrize("selection", ["information-hiding", "INFORMATION-HIDING"])
def test_cli_group_selection_needs_no_yaml_activation(
    tmp_path: Path, selection: str
) -> None:
    root = _group_project(tmp_path, "")
    assert violations(root) == []
    assert {
        v.code for v in violations(root, CheckOptions(select=(selection,)))
    } == _GROUP_CODES


def test_cli_group_selection_respects_off_and_ignore(tmp_path: Path) -> None:
    root = _group_project(tmp_path, "information-hiding: warning, SMT335: off")
    assert {
        v.code for v in violations(root, CheckOptions(select=("information-hiding",)))
    } == _GROUP_CODES - {"SMT335"}
    assert violations(root, CheckOptions(ignore=("information-hiding",))) == []
    _group_project(tmp_path, "information-hiding: off")
    assert violations(root, CheckOptions(select=("information-hiding",))) == []


def test_group_in_scoped_path_ignores(tmp_path: Path) -> None:
    root = _group_project(tmp_path)
    with (root / "smelt.yml").open("a", encoding="utf-8") as config:
        config.write(
            "ignore:\n  - rule: information-hiding\n    paths: ['app/api.py']\n    reason: Supported external API\n"
        )
    assert violations(root) == []


def test_package_group_and_closest_individual_override(tmp_path: Path) -> None:
    root = _group_project(tmp_path, "information-hiding: off")
    write_project(
        root,
        {
            "app/legacy/__init__.py": "",
            "app/legacy/api.py": "def helper(): return 1",
            "app/legacy/new/__init__.py": "",
            "app/legacy/new/api.py": "def helper(): return 1",
        },
    )
    with (root / "smelt.yml").open("a", encoding="utf-8") as config:
        config.write(
            "structure:\n  contracts:\n    legacy:\n      match: app.legacy\n      rules: {information-hiding: warning}\n    new:\n      match: app.legacy.new\n      rules: {SMT316: error}\n"
        )
    assert [(v.path, v.severity) for v in violations(root)] == [
        ("app/legacy/api.py", Severity.WARNING),
        ("app/legacy/new/api.py", Severity.ERROR),
    ]


def test_closest_group_overrides_parent_individual_setting(tmp_path: Path) -> None:
    root = _group_project(tmp_path, "information-hiding: warning, SMT335: error")
    with (root / "smelt.yml").open("a", encoding="utf-8") as config:
        config.write(
            "structure:\n  contracts:\n    api:\n      match: app\n      rules: {information-hiding: off}\n"
        )
    assert violations(root) == []


def test_group_configuration_preserves_stable_suppressions(tmp_path: Path) -> None:
    root = _group_project(tmp_path)
    write_project(
        root,
        {
            "app/api.py": _GROUP_SOURCE.replace(
                "self.pending = 0",
                "self.pending = 0  # smelt: ignore[SMT335] -- diagnostic API",
            )
        },
    )
    outcome = check(root)
    assert {v.code for v in outcome.report.violations} == _GROUP_CODES - {"SMT335"}
    assert outcome.report.suppressed == 1


def test_debt_survives_group_adoption_and_disable_is_unassessed(tmp_path: Path) -> None:
    root = _group_project(
        tmp_path, ", ".join(f"{code}: warning" for code in sorted(_GROUP_CODES))
    )
    previous = check(root)
    Debt.from_violations(
        previous.report.violations, snippet_reader(previous.context)
    ).write(root / "debt.json")
    _group_project(tmp_path)
    with (root / "smelt.yml").open("a", encoding="utf-8") as config:
        config.write("debt: debt.json\n")
    adopted = check(root)
    assert adopted.report.violations == []
    assert adopted.report.in_debt == 5
    assert adopted.resolved_debt == []
    _group_project(tmp_path, "information-hiding: off")
    with (root / "smelt.yml").open("a", encoding="utf-8") as config:
        config.write("debt: debt.json\n")
    assert check(root).resolved_debt == []


def test_group_selection_cannot_bypass_strict_coverage(tmp_path: Path) -> None:
    root = _group_project(tmp_path)
    outcome = check(
        root, CheckOptions(select=("information-hiding",), strict_coverage=True)
    )
    assert "SMT306" in {rule.code for rule, _ in outcome.active}


def test_editor_schema_exposes_the_same_group_at_project_and_package_level() -> None:
    definitions = config_json_schema()["$defs"]
    group = definitions["RuleSettings"]["properties"]["information-hiding"]
    assert (
        group == definitions["SourceRuleSettings"]["properties"]["information-hiding"]
    )
    assert group["enum"] == ["error", "warning", "hint", "off"]
    assert all(code in group["description"] for code in _GROUP_CODES)


def test_group_conflicts_with_an_individual_in_another_same_package_contract(
    tmp_path: Path,
) -> None:
    root = _group_project(tmp_path, "")
    with (root / "smelt.yml").open("a", encoding="utf-8") as config:
        config.write(
            "structure:\n  contracts:\n    policy:\n      match: app\n      rules: {information-hiding: warning}\n    exception:\n      match: app\n      rules: {SMT335: off}\n"
        )
    with pytest.raises(ConfigError, match="conflicting SMT335"):
        check(root)


def test_group_context_reports_effective_individual_settings(tmp_path: Path) -> None:
    root = _group_project(tmp_path, "information-hiding: warning, SMT335: off")
    outcome = check(root)
    briefing = build_briefing(
        outcome.context,
        resolve_target(outcome.context, "app/planned.py"),
        outcome.report,
    ).to_json()
    assert briefing["rules"]["internal-public-member"] == "warning"
    assert briefing["rules"]["internal-public-attribute"] == "off"
    assert briefing["rules"]["test-only-public-attribute"] == "warning"
