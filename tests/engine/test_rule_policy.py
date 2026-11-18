from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import check, codes_at, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

_CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
_IMPORT_RULE = "rules:\n  imports/forbidden-import-style: error\n"


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
def test_missing_policy_warns_at_the_named_setting(
    tmp_path: Path, code: str, *, policy: str, configured: str
) -> None:
    config = _CONFIG + f"rules:\n  {code}: error\n"
    root = write_project(tmp_path, {"smelt.yml": config, "app/__init__.py": ""})

    outcome = check(root, CheckOptions(fail_on=Severity.WARNING))

    [finding] = outcome.report.violations
    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT904",
        "smelt.yml",
        4,
        3,
    )
    assert finding.severity is Severity.WARNING
    assert finding.expected is not None
    assert finding.expected["setting"] == f"rules.{code}"
    assert finding.expected["policy"].endswith(f".{policy}")
    assert outcome.report.failed

    write_project(root, {"smelt.yml": config + configured})
    assert violations(root) == []


def test_inherited_named_setting_keeps_its_origin_under_a_family_cap(
    tmp_path: Path,
) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": _CONFIG + "extends: base.yml\nrules: {imports: hint}\n",
            "base.yml": _IMPORT_RULE,
            "app/__init__.py": "",
        },
    )

    assert codes_at(violations(root)) == [("SMT904", "base.yml", 2)]
    write_project(
        root, {"base.yml": "# Move the setting down one line\n" + _IMPORT_RULE}
    )
    assert codes_at(violations(root)) == [("SMT904", "base.yml", 3)]


def test_policy_for_another_package_does_not_cover_a_local_setting(
    tmp_path: Path,
) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": _CONFIG
            + """\
style:
  overrides:
    styled: {match: app.styled, imports: {style: absolute}}
    local:
      match: app.local
      rules:
        SMT308: warning
""",
            "app/__init__.py": "",
            "app/styled/__init__.py": "",
            "app/local/__init__.py": "",
        },
    )

    [finding] = violations(root)
    assert (finding.code, finding.path, finding.line) == ("SMT904", "smelt.yml", 9)
    assert finding.expected is not None
    assert finding.expected["setting"] == "style.overrides.local.rules.SMT308"


def test_workspace_rule_setting_points_to_the_owning_file(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {workspace: uv, source_roots: [src]}\n",
            "pyproject.toml": '[tool.uv.workspace]\nmembers = ["packages/*"]\n',
            "packages/app/smelt.yml": _IMPORT_RULE,
            "packages/app/src/app/__init__.py": "",
        },
    )

    assert codes_at(violations(root)) == [("SMT904", "packages/app/smelt.yml", 2)]


def test_an_off_override_and_cli_selection_alone_do_not_warn(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": _CONFIG
            + _IMPORT_RULE
            + "style: {overrides: {local: {match: app, rules: {SMT308: off}}}}\n",
            "app/__init__.py": "",
        },
    )

    assert violations(root) == []
    write_project(root, {"smelt.yml": _CONFIG})
    assert violations(root, CheckOptions(select=("SMT308", "SMT904"))) == []


def test_same_cache_supports_full_and_path_scoped_checks(tmp_path: Path) -> None:
    root = write_project(
        tmp_path, {"smelt.yml": _CONFIG + _IMPORT_RULE, "app/__init__.py": ""}
    )

    assert violations(root, CheckOptions(paths=("app",))) == []
    assert codes_at(violations(root)) == [("SMT904", "smelt.yml", 4)]
    assert violations(root, CheckOptions(paths=("app",))) == []


def test_yaml_merge_keeps_the_rule_key_location(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": _CONFIG + "rules:\n  <<:\n    SMT308: error\n",
            "app/__init__.py": "",
        },
    )

    assert codes_at(violations(root)) == [("SMT904", "smelt.yml", 5)]
