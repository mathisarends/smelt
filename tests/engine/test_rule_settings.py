from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError, load_config, parse_config
from smelt.config.errors import UPGRADE_GUIDE_URL
from smelt.diagnostics.debt import Debt
from smelt.diagnostics.violation import Severity
from smelt.engine.briefing import build_briefing, resolve_target
from smelt.engine.check import CheckOptions, snippet_reader
from smelt.engine.rule_settings import (
    RuleSettings,
    RuleStatus,
    RuleUsage,
    catalog_status,
    rule_usage,
    unknown_selectors,
)
from smelt.rules.registry import build_rule_set
from tests.helpers import (
    MEMBER_BOUNDARIES,
    check,
    violations,
    write_policy_project,
    write_project,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("value", ["off", "warning", "hint", "on"])
def test_member_weakening_requires_a_reason(tmp_path: Path, value: str) -> None:
    root = write_policy_project(
        tmp_path,
        {
            **MEMBER_BOUNDARIES,
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + "rules: {complexity/excess-positional-parameters: error}\n",
            "services/api/smelt.yml": f"rules: {{complexity/excess-positional-parameters: {value}}}\n",
        },
    )

    with pytest.raises(ConfigError, match=r"requires.*reason") as caught:
        check(root)

    assert caught.value.issues[0].source is not None
    assert caught.value.issues[0].source.endswith("services/api/smelt.yml")


def test_member_weakening_names_the_yaml_selector_once(tmp_path: Path) -> None:
    root = write_policy_project(
        tmp_path,
        {
            **MEMBER_BOUNDARIES,
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + "rules: {modeling/str-enum-value-convention: error, MOD005: warning}\n",
            "services/api/smelt.yml": "rules: {modeling: off}\n",
        },
    )

    with pytest.raises(ConfigError) as caught:
        check(root)

    [issue] = caught.value.issues
    assert issue.path == "rules.modeling"
    assert issue.source == "services/api/smelt.yml"
    assert str(caught.value).splitlines() == [
        "invalid configuration in services/api/smelt.yml",
        "  rules.modeling: lowering family modeling (2 rules) below the inherited root severity requires {severity: off, reason: '...'}",
    ]


@pytest.mark.parametrize(
    "policy",
    [
        "rules: {complexity: {severity: off, reason: Legacy API migration.}}\n",
        "style:\n  overrides:\n    api: {match: api, rules: {complexity/excess-positional-parameters: {severity: warning, reason: Legacy API migration.}}}\n",
    ],
)
def test_reasoned_member_and_contract_exceptions_are_accepted(
    tmp_path: Path, policy: str
) -> None:
    root = write_policy_project(
        tmp_path,
        {
            **MEMBER_BOUNDARIES,
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + "rules: {complexity/excess-positional-parameters: error}\n",
            "services/api/smelt.yml": policy,
        },
    )

    assert check(root).report.violations == []


def test_specific_member_entry_cannot_borrow_a_broader_reason(tmp_path: Path) -> None:
    root = write_policy_project(
        tmp_path,
        {
            **MEMBER_BOUNDARIES,
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"]
            + "rules: {complexity/excess-positional-parameters: error}\n",
            "services/api/smelt.yml": "rules:\n  complexity: {severity: off, reason: Legacy API migration.}\n  complexity/excess-positional-parameters: warning\n",
        },
    )

    with pytest.raises(ConfigError, match=r"CPX003.*reason"):
        check(root)


def test_an_inherited_root_rule_is_included_in_the_member_guard(
    tmp_path: Path,
) -> None:
    root = write_policy_project(
        tmp_path,
        {
            **MEMBER_BOUNDARIES,
            "smelt.yml": MEMBER_BOUNDARIES["smelt.yml"] + "extends: policy.yml\n",
            "policy.yml": "rules: {complexity/excess-positional-parameters: {severity: warning, max: 2}}\n",
            "services/api/smelt.yml": "rules: {complexity/excess-positional-parameters: off}\n",
        },
    )

    with pytest.raises(ConfigError, match=r"CPX003.*reason"):
        check(root)


def test_a_member_cannot_enable_the_workspace_coverage_guard(tmp_path: Path) -> None:
    root = write_policy_project(
        tmp_path,
        {
            **MEMBER_BOUNDARIES,
            "services/api/smelt.yml": "rules: {architecture/uncovered-source: error}\n",
        },
    )

    with pytest.raises(ConfigError, match="covers the whole workspace"):
        check(root)


@pytest.mark.parametrize("reason", ["''", "'  '"])
def test_blank_member_override_reasons_are_invalid(tmp_path: Path, reason: str) -> None:
    root = write_policy_project(
        tmp_path,
        {
            **MEMBER_BOUNDARIES,
            "services/api/smelt.yml": f"rules: {{complexity: {{severity: off, reason: {reason}}}}}\n",
        },
    )

    with pytest.raises(ConfigError, match="reason"):
        check(root)


def _project(tmp_path: Path, policy: str) -> Path:
    return write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n" + policy,
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
    root = _project(tmp_path, "rules: {visibility/internal-public-member: warning}\n")

    found = check(root).report.violations

    assert {v.path for v in found} == {
        "app/api.py",
        "app/legacy/api.py",
        "app/legacy/new/api.py",
    }
    assert all(
        v.rule == "visibility/internal-public-member" and v.severity == Severity.WARNING
        for v in found
    )


def test_package_only_activation_and_closest_off_and_severity(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        """
style:
  overrides:
    legacy:
      match: app.legacy
      rules: {visibility/internal-public-member: warning}
    new:
      match: app.legacy.new
      rules: {VIS001: error}
""",
    )

    found = check(root).report.violations

    assert [(v.path, v.severity) for v in found] == [
        ("app/legacy/api.py", Severity.WARNING),
        ("app/legacy/new/api.py", Severity.ERROR),
    ]
    with (root / "smelt.yml").open("a", encoding="utf-8") as target:
        target.write(
            "    disabled:\n      match: app.legacy.new\n      rules: {visibility/internal-public-member: off}\n"
        )
    with pytest.raises(ConfigError, match="conflicting VIS001"):
        check(root)


def test_closest_package_override_can_reenable_a_globally_disabled_rule(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        """
rules: {visibility/internal-public-member: off}
style:
  overrides:
    legacy:
      match: app.legacy
      rules: {visibility/internal-public-member: warning}
    new:
      match: app.legacy.new
      rules: {VIS001: off}
""",
    )

    outcome = check(root)

    assert [v.path for v in outcome.report.violations] == ["app/legacy/api.py"]
    briefing = build_briefing(
        outcome.context,
        resolve_target(outcome.context, "app/legacy/new/planned.py"),
        outcome.report,
    )
    assert briefing.to_json()["rules"]["visibility/internal-public-member"] == "off"
    assert (
        check(
            root, CheckOptions(ignore=("visibility/internal-public-member",))
        ).report.violations
        == []
    )


@pytest.mark.parametrize(
    "selector", ["VIS001", "vis001", "visibility/internal-public-member"]
)
def test_select_accepts_the_same_canonical_name_as_yaml(
    tmp_path: Path, selector: str
) -> None:
    root = _project(tmp_path, "")

    assert len(check(root, CheckOptions(select=(selector,))).report.violations) == 3
    assert check(root).report.violations == []


@pytest.mark.parametrize(
    "settings",
    [
        "VIS001: warning, visibility/internal-public-member: error",
        "internal-pubic-member: warning",
    ],
)
@pytest.mark.parametrize("scoped", [False, True])
def test_duplicate_aliases_and_typos_are_config_errors(
    tmp_path: Path, settings: str, scoped: bool
) -> None:
    policy = (
        f"style:\n  overrides:\n    api:\n      match: app\n      rules: {{{settings}}}\n"
        if scoped
        else f"rules: {{{settings}}}\n"
    )
    with pytest.raises(ConfigError, match=r"already configured|unknown rule"):
        check(_project(tmp_path, policy))


def test_scoped_off_does_not_report_old_debt_as_fixed(tmp_path: Path) -> None:
    root = _project(tmp_path, "rules: {visibility/internal-public-member: warning}\n")
    original = check(root)
    Debt.from_violations(
        original.report.violations, snippet_reader(original.context)
    ).write(root / "debt.json")
    with (root / "smelt.yml").open("a", encoding="utf-8") as target:
        target.write(
            "debt: debt.json\nstyle:\n  overrides:\n    legacy:\n      match: app.legacy\n      rules: {visibility/internal-public-member: off}\n"
        )

    outcome = check(root)

    assert outcome.report.violations == []
    assert outcome.resolved_debt == []


@pytest.mark.parametrize(
    "key",
    ["preset", "review_test_deadlines", "require_assertions", "member_visibility"],
)
def test_removed_switches_are_rejected_instead_of_silently_ignored(key: str) -> None:
    raw: dict[str, object] = {"version": 2, "project": {"root_packages": ["app"]}}
    if key == "preset":
        raw[key] = "maintainability"
    elif key == "member_visibility":
        raw["style"] = {
            "overrides": {"api": {"match": "app", key: "private-when-internal"}}
        }
    else:
        raw["tests"] = {key: True}
    with pytest.raises(ConfigError, match=key):
        parse_config(raw)


@pytest.mark.parametrize(
    "name",
    [
        "tests/unbounded-wait",
        "meta/unused-suppression",
        "architecture/uncovered-source",
    ],
)
def test_test_file_and_project_rules_cannot_silently_become_package_overrides(
    tmp_path: Path, name: str
) -> None:
    root = _project(
        tmp_path,
        f"style:\n  overrides:\n    api:\n      match: app\n      rules: {{{name}: off}}\n",
    )

    with pytest.raises(ConfigError, match="configured at project level"):
        check(root)


def test_reasoned_ignore_accepts_the_canonical_rule_name(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        """
rules: {visibility/internal-public-member: warning}
ignore:
  - rule: visibility/internal-public-member
    modules: ['app.legacy.**']
    reason: Supported legacy callbacks used outside this repository.
""",
    )

    assert [v.path for v in check(root).report.violations] == ["app/api.py"]


_VISIBILITY_CODES = {"VIS001", "VIS002", "VIS004", "VIS009", "VIS010"}
_VISIBILITY_SOURCE = """LIMIT = 3
def helper(): return LIMIT
def testing_hook(): return 0
class Worker:
    def __init__(self):
        self.pending = 0
        self.debug = 0
    def run(self): return helper() + self.pending
"""
_VISIBILITY_TEST = """from app.api import Worker, testing_hook
def test_api():
    worker = Worker()
    assert worker.run() == 3
    assert worker.debug == 0
    assert testing_hook() == 0
"""


_INDIVIDUAL_VISIBILITY = ", ".join(
    f"{code}: warning" for code in sorted(_VISIBILITY_CODES)
)


def _visibility_project(tmp_path: Path, policy: str = _INDIVIDUAL_VISIBILITY) -> Path:
    return write_policy_project(
        tmp_path,
        {
            "smelt.yml": f"version: 2\nproject: {{root_packages: [app]}}\nrules: {{{policy}}}\n",
            "app/__init__.py": "",
            "app/api.py": _VISIBILITY_SOURCE,
            "tests/test_api.py": _VISIBILITY_TEST,
        },
    )


def test_information_hiding_includes_test_only_apis_without_duplicates(
    tmp_path: Path,
) -> None:
    root = _visibility_project(tmp_path)
    outcome = check(root)
    findings = outcome.report.violations
    assert {v.code for v in findings} == _VISIBILITY_CODES
    assert len(findings) == 5
    assert all(v.severity == Severity.WARNING for v in findings)
    assert findings == violations(root, CheckOptions(use_cache=False))
    assert not {"LAY005", "LAY008"} & {rule.code for rule, _ in outcome.active}


def test_existing_individual_configuration_retains_identical_findings(
    tmp_path: Path,
) -> None:
    root = _visibility_project(tmp_path)
    grouped = violations(root, CheckOptions(select=("visibility",)))
    _visibility_project(
        tmp_path, ", ".join(f"{code}: warning" for code in sorted(_VISIBILITY_CODES))
    )
    assert violations(root) == grouped


@pytest.mark.parametrize(
    "policy",
    [
        "visibility: off, VIS001: warning",
        "VIS001: warning, visibility: off",
    ],
)
def test_specific_rule_overrides_group_regardless_of_key_order(
    tmp_path: Path, policy: str
) -> None:
    findings = violations(_visibility_project(tmp_path, policy))
    assert {v.code for v in findings} == {"VIS001"}


@pytest.mark.parametrize(
    "policy",
    [
        "visibility: off, VIS004: error",
        "VIS004: error, visibility: off",
    ],
)
def test_group_can_be_disabled_with_one_individual_exception(
    tmp_path: Path, policy: str
) -> None:
    [finding] = violations(_visibility_project(tmp_path, policy))
    assert (finding.code, finding.severity) == ("VIS004", Severity.ERROR)


def test_individual_severity_override(tmp_path: Path) -> None:
    findings = violations(
        _visibility_project(
            tmp_path,
            _INDIVIDUAL_VISIBILITY.replace(
                "VIS004: warning", "visibility/internal-public-attribute: hint"
            ),
        )
    )
    assert {v.code for v in findings} == _VISIBILITY_CODES
    assert {v.severity for v in findings if v.code == "VIS004"} == {Severity.HINT}
    assert {v.severity for v in findings if v.code != "VIS004"} == {Severity.WARNING}


def test_group_does_not_allow_duplicate_individual_aliases(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="VIS004 is already configured"):
        check(
            _visibility_project(
                tmp_path,
                "visibility: off, VIS004: off, visibility/internal-public-attribute: warning",
            )
        )


@pytest.mark.parametrize("selection", ["visibility", "VISIBILITY"])
def test_cli_group_selection_needs_no_yaml_activation(
    tmp_path: Path, selection: str
) -> None:
    root = _visibility_project(tmp_path, "")
    assert violations(root) == []
    assert {
        v.code for v in violations(root, CheckOptions(select=(selection,)))
    } == _VISIBILITY_CODES


def test_cli_group_selection_respects_off_and_ignore(tmp_path: Path) -> None:
    root = _visibility_project(
        tmp_path, _INDIVIDUAL_VISIBILITY.replace("VIS004: warning", "VIS004: off")
    )
    assert {
        v.code for v in violations(root, CheckOptions(select=("visibility",)))
    } == _VISIBILITY_CODES - {"VIS004"}
    assert violations(root, CheckOptions(ignore=("visibility",))) == []
    _visibility_project(tmp_path, "visibility: off")
    assert violations(root, CheckOptions(select=("visibility",))) == []


def test_family_in_scoped_path_ignores(tmp_path: Path) -> None:
    root = _visibility_project(tmp_path)
    with (root / "smelt.yml").open("a", encoding="utf-8") as config:
        config.write(
            "ignore:\n  - rule: visibility\n    paths: ['app/api.py']\n    reason: Supported external API\n"
        )
    assert violations(root) == []


def test_package_group_and_closest_individual_override(tmp_path: Path) -> None:
    root = _visibility_project(tmp_path, "visibility: off")
    write_policy_project(
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
            "style:\n  overrides:\n    legacy:\n      match: app.legacy\n      rules: {VIS001: warning}\n    new:\n      match: app.legacy.new\n      rules: {VIS001: error}\n"
        )
    assert [(v.path, v.severity) for v in violations(root)] == [
        ("app/legacy/api.py", Severity.WARNING),
        ("app/legacy/new/api.py", Severity.ERROR),
    ]


def test_closest_group_overrides_parent_individual_setting(tmp_path: Path) -> None:
    root = _visibility_project(
        tmp_path, _INDIVIDUAL_VISIBILITY.replace("VIS004: warning", "VIS004: error")
    )
    with (root / "smelt.yml").open("a", encoding="utf-8") as config:
        config.write(
            "style:\n  overrides:\n    api:\n      match: app\n      rules: {visibility: off}\n"
        )
    assert violations(root) == []


def test_group_configuration_preserves_stable_suppressions(tmp_path: Path) -> None:
    root = _visibility_project(tmp_path)
    write_policy_project(
        root,
        {
            "app/api.py": _VISIBILITY_SOURCE.replace(
                "self.pending = 0",
                "self.pending = 0  # smelt: ignore[VIS004] -- diagnostic API",
            )
        },
    )
    outcome = check(root)
    assert {v.code for v in outcome.report.violations} == _VISIBILITY_CODES - {"VIS004"}
    assert outcome.report.suppressed == 1


def test_debt_survives_group_adoption_and_disable_is_unassessed(tmp_path: Path) -> None:
    root = _visibility_project(
        tmp_path, ", ".join(f"{code}: warning" for code in sorted(_VISIBILITY_CODES))
    )
    previous = check(root)
    Debt.from_violations(
        previous.report.violations, snippet_reader(previous.context)
    ).write(root / "debt.json")
    _visibility_project(tmp_path)
    with (root / "smelt.yml").open("a", encoding="utf-8") as config:
        config.write("debt: debt.json\n")
    adopted = check(root)
    assert adopted.report.violations == []
    assert adopted.report.in_debt == 5
    assert adopted.resolved_debt == []
    _visibility_project(tmp_path, "visibility: off")
    with (root / "smelt.yml").open("a", encoding="utf-8") as config:
        config.write("debt: debt.json\n")
    assert check(root).resolved_debt == []


def test_group_selection_cannot_bypass_strict_coverage(tmp_path: Path) -> None:
    root = _visibility_project(tmp_path)
    outcome = check(root, CheckOptions(select=("visibility",), strict_coverage=True))
    assert "ARC013" in {rule.code for rule, _ in outcome.active}


def test_group_conflicts_with_an_individual_in_another_same_package_contract(
    tmp_path: Path,
) -> None:
    root = _visibility_project(tmp_path, "")
    with (root / "smelt.yml").open("a", encoding="utf-8") as config:
        config.write(
            "style:\n  overrides:\n    policy:\n      match: app\n      rules: {VIS004: warning}\n    exception:\n      match: app\n      rules: {VIS004: off}\n"
        )
    with pytest.raises(ConfigError, match="conflicting VIS004"):
        check(root)


def test_group_context_reports_effective_individual_settings(tmp_path: Path) -> None:
    root = _visibility_project(
        tmp_path, _INDIVIDUAL_VISIBILITY.replace("VIS004: warning", "VIS004: off")
    )
    outcome = check(root)
    briefing = build_briefing(
        outcome.context,
        resolve_target(outcome.context, "app/planned.py"),
        outcome.report,
    ).to_json()
    assert briefing["rules"]["visibility/internal-public-member"] == "warning"
    assert briefing["rules"]["visibility/internal-public-attribute"] == "off"
    assert briefing["rules"]["visibility/test-only-public-attribute"] == "warning"


def _catalog() -> dict[str, Severity]:
    return {rule.code: rule.default_severity for rule in build_rule_set().rules}


def _severities(root: Path, options: CheckOptions | None = None) -> dict[str, Severity]:
    outcome = check(root, options) if options else check(root)
    return {rule.code: severity for rule, severity in outcome.active}


@pytest.mark.parametrize("policy", ["ALL: on", "typing: warning", "visibility: hint"])
def test_broad_yaml_entry_cannot_enable_rules(tmp_path: Path, policy: str) -> None:
    root = _visibility_project(tmp_path, policy)

    with pytest.raises(ConfigError, match="cannot enable rules"):
        check(root)


def test_all_remains_available_for_cli_exploration(tmp_path: Path) -> None:
    root = _visibility_project(tmp_path, "")

    assert _severities(root, CheckOptions(select=("ALL",))) == _catalog()


def test_family_caps_only_active_rules_in_a_base(tmp_path: Path) -> None:
    root = _visibility_project(tmp_path, "")
    write_policy_project(
        tmp_path,
        {
            "base.yml": "rules: {VIS001: error, VIS004: hint}\n",
            "smelt.yml": "extends: base.yml\nversion: 2\n"
            "project: {root_packages: [app]}\nrules: {visibility: warning, VIS002: error}\n",
        },
    )

    active = _severities(root)

    assert active["VIS001"] == Severity.WARNING
    assert active["VIS004"] == Severity.HINT
    assert active["VIS002"] == Severity.ERROR
    assert "VIS009" not in active


def test_broad_selection_leaves_strict_coverage_an_error(tmp_path: Path) -> None:
    root = _visibility_project(tmp_path, "")
    write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n"
            "architecture: {layers: {domain: {path: domain}}}\n"
            "rules:\n"
            "  architecture/uncovered-source: error\n"
            "  architecture/layer-boundary: error\n"
            "  architecture: warning\n"
            "  ALL: hint\n",
        },
    )

    active = _severities(root)

    assert active["ARC013"] == Severity.ERROR
    # The named rule wins over the broad selectors of its own layer.
    assert active["ARC001"] == Severity.ERROR


def test_broad_package_selection_only_selects_source_rules(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "style:\n  overrides:\n    legacy:\n      match: app.legacy\n"
        "      rules: {ALL: off, tests: off, visibility/internal-public-member: warning}\n",
    )

    found = check(root).report.violations

    assert {v.path for v in found if v.code == "VIS001"} == {
        "app/legacy/api.py",
        "app/legacy/new/api.py",
    }


def test_broad_override_error_names_the_style_key(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "style:\n  overrides:\n    legacy:\n      match: app.legacy\n"
        "      rules: {visibility: warning}\n",
    )

    with pytest.raises(ConfigError, match="cannot enable rules") as caught:
        check(root)

    assert caught.value.issues[0].path == "style.overrides.legacy.rules.visibility"


def test_a_broad_child_setting_does_not_reactivate_an_off_rule(tmp_path: Path) -> None:
    root = _visibility_project(tmp_path, "")
    write_policy_project(
        tmp_path,
        {
            "base.yml": "rules: {VIS001: error, visibility/internal-public-attribute: off}\n",
            "smelt.yml": "extends: base.yml\nversion: 2\n"
            "project: {root_packages: [app]}\nrules: {visibility: warning}\n",
        },
    )

    assert {v.code for v in violations(root)} == {"VIS001"}


_WORKSPACE = {
    "pyproject.toml": '[tool.uv.workspace]\nmembers = ["services/*", "packages/*"]\n',
    "smelt.yml": "version: 2\nproject: {workspace: uv, source_roots: [src], test_roots: [tests]}\n",
    "services/api/src/api/__init__.py": "",
    "services/api/src/api/views.py": "def helper(): return 1\n",
    "services/api/src/api/legacy/__init__.py": "def helper(): return 1\n",
    "services/api/tests/test_views.py": "def test_views():\n    print(1)\n",
    "packages/core/src/core/__init__.py": "",
    "packages/core/src/core/views.py": "def helper(): return 1\n",
    "packages/core/tests/test_core.py": "def test_core():\n    print(1)\n",
    "tests/test_e2e.py": "def test_e2e():\n    print(1)\n",
}


def _members(tmp_path: Path, files: dict[str, str]) -> Path:
    return write_policy_project(tmp_path, {**_WORKSPACE, **files})


def test_a_member_rule_setting_refines_a_broad_root_cap(tmp_path: Path) -> None:
    source = "def helper(a, b, c, d): return a + b + c + d\n"
    root = _members(
        tmp_path,
        {
            "smelt.yml": _WORKSPACE["smelt.yml"] + "rules: {complexity: hint}\n",
            "services/api/smelt.yml": "rules: {complexity/excess-positional-parameters: {severity: warning, max: 3}}\n",
            "services/api/src/api/views.py": source,
            "packages/core/src/core/views.py": source,
        },
    )

    [finding] = violations(root)
    assert (finding.code, finding.member, finding.severity) == (
        "CPX003",
        "services/api",
        Severity.WARNING,
    )


def test_member_rules_reach_its_sources_and_tests_only(tmp_path: Path) -> None:
    root = _members(
        tmp_path,
        {
            "services/api/smelt.yml": "rules:\n"
            "  visibility/internal-public-member: warning\n"
            "  tests/missing-outcome-check: hint\n"
        },
    )

    found = {(v.rule, v.path, v.severity) for v in check(root).report.violations}

    assert found == {
        (
            "visibility/internal-public-member",
            "services/api/src/api/legacy/__init__.py",
            Severity.WARNING,
        ),
        (
            "visibility/internal-public-member",
            "services/api/src/api/views.py",
            Severity.WARNING,
        ),
        (
            "tests/missing-outcome-check",
            "services/api/tests/test_views.py",
            Severity.HINT,
        ),
    }


def test_a_package_contract_beats_the_member_setting(tmp_path: Path) -> None:
    root = _members(
        tmp_path,
        {
            "services/api/smelt.yml": "rules: {visibility/internal-public-member: warning}\n"
            "style:\n  overrides:\n    legacy:\n      match: api.legacy\n"
            "      rules: {visibility/internal-public-member: off}\n"
        },
    )

    found = {v.path for v in check(root).report.violations}

    assert found == {"services/api/src/api/views.py"}


def test_a_member_cannot_relax_strict_coverage(tmp_path: Path) -> None:
    root = _members(
        tmp_path,
        {
            "smelt.yml": _WORKSPACE["smelt.yml"]
            + "rules: {architecture/uncovered-source: error}\n",
            "services/api/smelt.yml": "rules: {ARC013: warning}\n",
        },
    )

    with pytest.raises(ConfigError, match="strict coverage must remain") as caught:
        check(root)

    assert caught.value.issues[0].path == "rules.ARC013"


def test_rule_and_ignore_selector_mistakes_are_reported_together(
    tmp_path: Path,
) -> None:
    policy = (
        "rules: {import-cycle: error, typing/no-such-rule: hint}\n"
        "ignore:\n"
        "  - {rule: private-test-access, paths: [app/api.py], reason: legacy}\n"
        "  - {rule: yaml-file-extension, paths: [app/api.py], reason: legacy}\n"
    )

    with pytest.raises(ConfigError) as caught:
        check(_project(tmp_path, policy))

    assert [issue.path for issue in caught.value.issues] == [
        "rules.import-cycle",
        "rules.typing/no-such-rule",
        "ignore[0].rule",
        "ignore[1].rule",
    ]


def test_unknown_selectors_of_an_unvalidated_config() -> None:
    raw = {
        "rules": {"ALL": "off", "imports": "hint", "import-cycle": "error"},
        "ignore": [{"rule": "ARC"}, {"rule": "private-test-access"}, "broken"],
        "structure": {},
    }

    found = unknown_selectors(raw, build_rule_set())

    assert [(issue.path, issue.message) for issue in found] == [
        (
            "rules.import-cycle",
            'unknown rule "import-cycle"; renamed to "architecture/import-cycle" '
            f"in 0.13.0 (see {UPGRADE_GUIDE_URL})",
        ),
        (
            "ignore[1].rule",
            'unknown rule "private-test-access"; renamed to "tests/private-access" '
            f"in 0.13.0 (see {UPGRADE_GUIDE_URL})",
        ),
    ]


@pytest.mark.parametrize(
    ("policy", "location", "message"),
    [
        (
            "rules: {complexity: {severity: hint, max: 3}}",
            "rules.complexity.max",
            "max belongs to an individual rule",
        ),
        (
            "rules: {architecture/import-cycle: {severity: error, max: 3}}",
            "rules.architecture/import-cycle.max",
            "this rule takes no max",
        ),
        (
            "rules: {complexity/excess-comprehension-loops: {severity: hint, max: 0}}",
            "rules.complexity/excess-comprehension-loops.max",
            "max must be at least 1",
        ),
        (
            "rules: {complexity/excess-positional-parameters: {max: 3}}",
            "rules.complexity/excess-positional-parameters",
            "is not enabled; add its severity, e.g. {severity: warning, max: 3}",
        ),
        (
            "rules: {tests/excess-patches: error}\nstyle:\n  overrides:\n"
            "    api: {match: app, rules: {tests/excess-patches: {max: 1}}}",
            "style.overrides.api.rules.tests/excess-patches.max",
            "this limit applies to the whole project",
        ),
    ],
)
def test_a_max_is_validated_where_it_is_written(
    tmp_path: Path, policy: str, location: str, message: str
) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": f"version: 2\nproject: {{root_packages: [app]}}\n{policy}\n",
            "app/__init__.py": "",
        },
    )

    with pytest.raises(ConfigError, match=re.escape(message)) as caught:
        check(root)

    assert location in {issue.path for issue in caught.value.issues}


def test_a_package_max_refines_the_root_limit_and_keeps_its_severity(
    tmp_path: Path,
) -> None:
    source = "def wide(a, b, c):\n    return a\n"
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": """\
version: 2
project: {root_packages: [app]}
rules: {complexity/excess-positional-parameters: {severity: error, max: 2}}
style:
  overrides:
    legacy:
      match: app.legacy
      rules: {complexity/excess-positional-parameters: {max: 3}}
""",
            "app/__init__.py": "",
            "app/core.py": source,
            "app/legacy/__init__.py": "",
            "app/legacy/core.py": source,
        },
    )

    assert [(v.path, v.severity) for v in violations(root)] == [
        ("app/core.py", Severity.ERROR)
    ]


def test_retired_smt_code_is_a_config_error_naming_its_family_code(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, "rules: {SMT316: error}\n")

    with pytest.raises(ConfigError, match="renamed to VIS001"):
        check(root)


def test_status_tells_project_wide_scoped_off_and_missing_policy_apart(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": """\
version: 2
project: {root_packages: [app]}
rules: {complexity/excess-positional-parameters: error}
style:
  overrides:
    api: {match: app, rules: {visibility/internal-public-member: warning}}
""",
            "app/__init__.py": "",
        },
    )
    rules = build_rule_set()
    settings = RuleSettings(rules, load_config(tmp_path / "smelt.yml"))

    statuses = {
        code: settings.status(rule)
        for code in ("CPX003", "VIS001", "TYP007", "ARC001")
        if (rule := rules.lookup(code)) is not None
    }

    assert statuses == {
        "CPX003": RuleStatus("on", "error"),
        "VIS001": RuleStatus("scoped"),
        "TYP007": RuleStatus("off"),
        "ARC001": RuleStatus("needs policy"),
    }


def test_catalog_status_applies_without_a_configuration() -> None:
    rules = build_rule_set()

    statuses = [
        catalog_status(rule)
        for code in ("ARC011", "ARC001", "TYP007")
        if (rule := rules.lookup(code)) is not None
    ]

    assert statuses == [
        RuleStatus("on", "error"),
        RuleStatus("needs policy"),
        RuleStatus("off"),
    ]


@pytest.mark.parametrize(
    ("code", "usage"),
    [
        (
            "CPX003",
            RuleUsage(
                "rules: {complexity/excess-positional-parameters: "
                "{severity: warning, max: 5}}",
                ("max (default 5)", "style.overrides"),
                None,
            ),
        ),
        (
            "TST005",
            RuleUsage(
                "rules: {tests/excess-patches: {severity: error, max: <n>}}",
                ("max (required)", "tests.quality_exclude"),
                "The tests/excess-patches entry sets max, including zero.",
            ),
        ),
        (
            "ARC011",
            RuleUsage(
                "on by default; turn it off with rules: {architecture/unknown-layer: off}",
                ("architecture.layers", "architecture.layers.<layer>.modules"),
                None,
            ),
        ),
    ],
)
def test_rule_usage_names_the_entry_options_and_required_policy(
    code: str, usage: RuleUsage
) -> None:
    rule = build_rule_set().lookup(code)
    assert rule is not None

    assert rule_usage(rule) == usage
