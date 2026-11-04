from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError
from smelt.engine.check import CheckOptions
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"


@pytest.mark.parametrize("extension", ["yaml", "yml"])
def test_yaml_extension_is_a_single_project_policy(
    tmp_path: Path, extension: str
) -> None:
    other = "yml" if extension == "yaml" else "yaml"
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG
            + f"structure: {{file_naming: {{yaml_extension: {extension}}}}}\n",
            "app/__init__.py": "",
            f"settings.{extension}": "not parsed: [",
            f".github/workflows/ci.{other}": "not parsed: [",
            f"deploy/nested/settings.{other}": "",
            f"deploy/compose.{other}": "services: {}",
            "settings.yaml.bak": "",
            f".venv/vendor.{other}": "",
            f"node_modules/vendor.{other}": "",
            f".smelt_cache/vendor.{other}": "",
        },
    )
    report = check(tmp_path).report
    expected_paths = {
        f".github/workflows/ci.{other}",
        f"deploy/nested/settings.{other}",
        f"deploy/compose.{other}",
    } | ({"smelt.yml"} if extension == "yaml" else set())
    assert {v.path for v in report.violations} == expected_paths
    assert {v.code for v in report.violations} == {"SMT331"}
    assert all(v.line is None for v in report.violations)
    assert all(
        v.expected is not None and v.expected["yaml_extension"] == extension
        for v in report.violations
    )
    assert report.checked_files == 6


@pytest.mark.parametrize("basename", ["compose", "docker-compose"])
def test_compose_basename_preserves_variants_and_does_not_choose_an_extension(
    tmp_path: Path, basename: str
) -> None:
    other = "docker-compose" if basename == "compose" else "compose"
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG
            + f"structure: {{file_naming: {{compose_basename: {basename}}}}}\n",
            "app/__init__.py": "",
            f"{basename}.yaml": "",
            f"{basename}.override.yml": "",
            f"{other}.yml": "",
            f"deploy/{other}.prod.eu.yaml": "",
            "docker-compose-backup.yml": "",
            "compose-helper.yaml": "",
            "stack.yaml": "services: {}",
        },
    )
    found = check(tmp_path).report.violations
    assert {(v.code, v.path, (v.expected or {})["path"]) for v in found} == {
        ("SMT332", f"{other}.yml", f"{basename}.yml"),
        ("SMT332", f"deploy/{other}.prod.eu.yaml", f"deploy/{basename}.prod.eu.yaml"),
    }


def test_both_policies_report_independent_parts_and_existing_destinations(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG
            + "structure: {file_naming: {yaml_extension: yml, compose_basename: compose}}\n",
            "app/__init__.py": "",
            "deploy/docker-compose.override.yaml": "",
            "deploy/compose.override.yml": "",
        },
    )
    found = check(tmp_path).report.violations
    assert [(v.code, (v.expected or {})["path"]) for v in found] == [
        ("SMT331", "deploy/docker-compose.override.yml"),
        ("SMT332", "deploy/compose.override.yaml"),
    ]
    assert (tmp_path / "deploy/docker-compose.override.yaml").exists()
    assert (tmp_path / "deploy/compose.override.yml").exists()


@pytest.mark.parametrize("policy", ["", "structure: {file_naming: {}}\n"])
def test_missing_policies_allow_both_spellings_even_when_selected(
    tmp_path: Path, policy: str
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG + policy,
            "app/__init__.py": "",
            "compose.yml": "",
            "docker-compose.yaml": "",
        },
    )
    outcome = check(tmp_path, CheckOptions(select=("SMT33",)))
    assert not outcome.report.violations


def test_exclusions_ignores_severity_and_scope_apply_to_yaml(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG.replace(
                "root_packages: [app]", "root_packages: [app], exclude: ['vendor/**']"
            )
            + "structure: {file_naming: {yaml_extension: yml, compose_basename: compose}}\n"
            + "rules: {yaml-file-extension: warning, compose-file-name: off}\n"
            + "ignore:\n  - {rule: SMT331, paths: ['special.yaml'], reason: External contract}\n",
            "app/__init__.py": "",
            "vendor/docker-compose.yaml": "",
            "special.yaml": "",
            "deploy/docker-compose.yaml": "",
            "other.yaml": "",
        },
    )
    report = check(tmp_path, CheckOptions(paths=("deploy/docker-compose.yaml",))).report
    assert [(v.code, v.path, v.severity.value) for v in report.violations] == [
        ("SMT331", "deploy/docker-compose.yaml", "warning")
    ]
    assert report.checked_files == 1


def test_yaml_renames_additions_and_deletions_invalidate_cached_findings(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG
            + "structure: {file_naming: {yaml_extension: yml, compose_basename: compose}}\n",
            "app/__init__.py": "",
            "compose.yml": "",
        },
    )
    assert not check(tmp_path).report.violations
    (tmp_path / "compose.yml").rename(tmp_path / "docker-compose.yaml")
    cached = check(tmp_path).report
    fresh = check(tmp_path, CheckOptions(use_cache=False)).report
    assert cached.violations == fresh.violations
    assert [v.code for v in cached.violations] == ["SMT331", "SMT332"]
    (tmp_path / "docker-compose.yaml").unlink()
    assert not check(tmp_path).report.violations


@pytest.mark.parametrize(
    "policy",
    ["yaml_extension: json", "yaml_extension: .yaml", "compose_basename: stack"],
)
def test_invalid_filename_policy_is_rejected(tmp_path: Path, policy: str) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG + f"structure: {{file_naming: {{{policy}}}}}\n",
            "app/__init__.py": "",
        },
    )
    with pytest.raises(ConfigError, match=r"structure\.file_naming"):
        check(tmp_path)
