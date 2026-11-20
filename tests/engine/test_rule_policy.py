from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config.errors import ConfigError
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import check, codes_at, violations, write_policy_project

if TYPE_CHECKING:
    from pathlib import Path

_CONFIG = "version: 2\nproject: {root_packages: [app]}\n"
_IMPORT_RULE = "rules:\n  imports/forbidden-import-style: error\n"
# A policy for app.styled only: a setting scoped to app.local stays uncovered.
_STYLED_POLICY = "    styled: {match: app.styled, imports: {style: absolute}}\n"
_LOCAL = (
    "style:\n  overrides:\n"
    + _STYLED_POLICY
    + "    local:\n      match: app.local\n      rules:\n        {setting}\n"
)
_PACKAGES = {
    "app/__init__.py": "",
    "app/styled/__init__.py": "",
    "app/local/__init__.py": "",
}


def _local(setting: str = "SMT308: warning") -> str:
    """A config whose app.local override names a rule without its policy (line 9)."""
    return _CONFIG + _LOCAL.replace("{setting}", setting)


@pytest.mark.parametrize(
    ("code", "policy", "configured"),
    [
        (
            "SMT107",
            "public",
            "architecture: {packages: {api: {match: app, public: []}}}\n",
        ),
        (
            "SMT108",
            "min_imports_to_bundle",
            "architecture: {packages: {api: {match: app, min_imports_to_bundle: 2}}}\n",
        ),
        (
            "SMT109",
            "min_importers_to_export",
            "architecture: {packages: {api: {match: app, min_importers_to_export: 2}}}\n",
        ),
        ("SMT308", "imports", "style: {imports: {style: absolute}}\n"),
    ],
)
def test_a_named_rule_without_any_policy_is_a_config_error(
    tmp_path: Path, code: str, *, policy: str, configured: str
) -> None:
    config = _CONFIG + f"rules:\n  {code}: error\n"
    root = write_policy_project(tmp_path, {"smelt.yml": config, "app/__init__.py": ""})

    with pytest.raises(ConfigError, match="policy is missing") as caught:
        check(root)

    assert caught.value.issues[0].path == f"rules.{code}"
    assert policy in configured
    write_policy_project(root, {"smelt.yml": config + configured})
    assert violations(root) == []


def test_missing_policy_warns_at_the_uncovered_setting(tmp_path: Path) -> None:
    root = write_policy_project(tmp_path, {"smelt.yml": _local(), **_PACKAGES})

    outcome = check(root, CheckOptions(fail_on=Severity.WARNING))

    [finding] = outcome.report.violations
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT904",
        "smelt.yml",
        9,
        9,
    )
    assert finding.severity is Severity.WARNING
    assert finding.expected is not None
    assert finding.expected["setting"] == "style.overrides.local.rules.SMT308"
    assert finding.expected["policy"] == "style.imports"
    assert outcome.report.failed


def test_an_inherited_rule_without_any_policy_names_its_origin(
    tmp_path: Path,
) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": _CONFIG + "extends: base.yml\nrules: {imports: hint}\n",
            "base.yml": _IMPORT_RULE,
            "app/__init__.py": "",
        },
    )

    with pytest.raises(ConfigError, match="policy is missing") as caught:
        check(root)

    assert caught.value.source == "base.yml"


def test_workspace_rule_setting_points_to_the_owning_file(tmp_path: Path) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {workspace: uv, source_roots: [src]}\n",
            "pyproject.toml": '[tool.uv.workspace]\nmembers = ["packages/*"]\n',
            "packages/app/smelt.yml": _IMPORT_RULE,
            "packages/app/src/app/__init__.py": "",
            "packages/other/smelt.yml": "style: {imports: {style: absolute}}\n",
            "packages/other/src/other/__init__.py": "",
        },
    )

    assert codes_at(violations(root)) == [("SMT904", "packages/app/smelt.yml", 2)]


def test_an_off_override_and_cli_selection_alone_do_not_warn(tmp_path: Path) -> None:
    root = write_policy_project(
        tmp_path, {"smelt.yml": _local("SMT308: off"), **_PACKAGES}
    )

    assert violations(root) == []
    write_policy_project(root, {"smelt.yml": _CONFIG})
    assert violations(root, CheckOptions(select=("SMT308", "SMT904"))) == []


def test_same_cache_supports_full_and_path_scoped_checks(tmp_path: Path) -> None:
    root = write_policy_project(tmp_path, {"smelt.yml": _local(), **_PACKAGES})

    assert violations(root, CheckOptions(paths=("app",))) == []
    assert codes_at(violations(root)) == [("SMT904", "smelt.yml", 9)]
    assert violations(root, CheckOptions(paths=("app",))) == []


def test_yaml_merge_keeps_the_rule_key_location(tmp_path: Path) -> None:
    root = write_policy_project(
        tmp_path, {"smelt.yml": _local("<<:\n          SMT308: error"), **_PACKAGES}
    )

    assert codes_at(violations(root)) == [("SMT904", "smelt.yml", 10)]
