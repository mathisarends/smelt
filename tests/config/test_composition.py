from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.config import ConfigError, load_config
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path


def test_shared_policy_is_composed_before_defaults_and_workspace_discovery(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "policy/base.yml": "version: 1\nrules: {import-cycle: error, unbounded-test-wait: warning}\nsuppressions: {require_reason: false}\nproject: {source_roots: [src], test_roots: [tests]}\n",
            "first/smelt.yml": "extends: ../policy/base.yml\nproject: {coverage: strict}\narchitecture: {modules: {first: domain}, layers: {domain: {path: domain}}}\n",
            "second/smelt.yml": "extends: ../policy/base.yml\nrules: {unbounded-test-wait: error}\narchitecture: {modules: {second: domain}, layers: {domain: {path: domain}}}\n",
            "first/src/first/__init__.py": "",
            "second/src/second/__init__.py": "",
        },
    )

    first = load_config(tmp_path / "first/smelt.yml")
    second = load_config(tmp_path / "second/smelt.yml")

    assert first.config.rules == {
        "import-cycle": "error",
        "unbounded-test-wait": "warning",
    }
    assert second.config.rules == {
        "import-cycle": "error",
        "unbounded-test-wait": "error",
    }
    assert first.config.project.root_packages == ["first"]
    assert second.config.project.root_packages == ["second"]
    assert first.config.project.coverage == "strict"
    assert second.config.project.coverage == "configured"
    assert first.config.suppressions.require_reason is False
    assert first.inputs == (
        (tmp_path / "policy/base.yml").resolve(),
        (tmp_path / "first/smelt.yml").resolve(),
    )
    assert second.origins["rules.import-cycle"].endswith("policy/base.yml")
    assert second.origins["rules.unbounded-test-wait"].endswith("second/smelt.yml")


def test_nested_parent_paths_and_explicit_empty_lists_replace(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "base.yml": "version: 1\nproject: {root_packages: [app], exclude: [generated/**]}\narchitecture:\n  layers:\n    domain: {path: domain, may_depend_on: [application]}\n    application: {path: application}\ntests: {quality_exclude: [tests/generated/**]}\nignore: [{rule: SMT101, paths: ['**']}]\n",
            "policy/team.yml": "extends: ../base.yml\narchitecture: {layers: {domain: {may_depend_on: []}}}\n",
            "smelt.yml": "extends: policy/team.yml\nproject: {exclude: []}\ntests: {quality_exclude: []}\nignore: []\n",
        },
    )
    loaded = load_config(tmp_path / "smelt.yml")

    assert loaded.config.architecture.layers["domain"].path == "domain"
    assert loaded.config.architecture.layers["domain"].may_depend_on == []
    assert loaded.config.project.exclude == []
    assert loaded.config.tests.quality_exclude == []
    assert loaded.config.ignore == []
    assert len(loaded.inputs) == 3
    assert loaded.origins["architecture.layers.domain.may_depend_on"].endswith(
        "policy/team.yml"
    )
    assert "project.exclude[0]" not in loaded.origins


@pytest.mark.parametrize(
    "value", ["[]", "null", "''", "'https://example.com/policy.yml'", "42"]
)
def test_extends_requires_one_local_relative_path(tmp_path: Path, value: str) -> None:
    write_project(tmp_path, {"smelt.yml": f"extends: {value}\n"})

    with pytest.raises(ConfigError, match="local relative file path") as caught:
        load_config(tmp_path / "smelt.yml")

    assert caught.value.issues[0].path == "extends"
    assert caught.value.source is not None
    assert caught.value.source.endswith("smelt.yml")


def test_missing_parent_names_the_referring_file_and_key(tmp_path: Path) -> None:
    write_project(tmp_path, {"smelt.yml": "extends: missing.yml\n"})

    with pytest.raises(ConfigError, match=r"cannot read.*missing.yml") as caught:
        load_config(tmp_path / "smelt.yml")

    assert caught.value.issues[0].path == "extends"
    assert caught.value.source is not None
    assert caught.value.source.endswith("smelt.yml")


def test_cycles_report_the_chain_even_with_equivalent_paths(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "extends: policy/base.yml\n",
            "policy/base.yml": "extends: ../policy/../smelt.yml\n",
        },
    )

    with pytest.raises(ConfigError, match="inheritance cycle") as caught:
        load_config(tmp_path / "smelt.yml")

    assert caught.value.issues[0].path == "extends"
    assert caught.value.source is not None
    assert caught.value.source.endswith("policy/base.yml")
    assert str(caught.value).count("smelt.yml") == 2


@pytest.mark.parametrize(
    ("base", "location"),
    [
        ("rules: {import-cycle: banana}", "rules.import-cycle"),
        ("architecture: {layerz: {}}", "architecture.layerz"),
        ("version: 2", "version"),
    ],
)
def test_effective_validation_reports_the_base_origin(
    tmp_path: Path, base: str, location: str
) -> None:
    write_project(
        tmp_path,
        {
            "base.yml": base,
            "smelt.yml": "extends: base.yml\nproject: {root_packages: [app]}\n"
            + ("" if base.startswith("version") else "version: 1\n"),
        },
    )

    with pytest.raises(ConfigError) as caught:
        load_config(tmp_path / "smelt.yml")

    [issue] = caught.value.issues
    assert issue.path == location
    assert issue.source is not None
    assert issue.source.endswith("base.yml")


def test_inherited_duplicate_yaml_keys_are_still_errors(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "base.yml": "rules: {import-cycle: error, import-cycle: off}\n",
            "smelt.yml": "extends: base.yml\nversion: 1\nproject: {root_packages: [app]}\n",
        },
    )

    with pytest.raises(ConfigError, match="duplicate key") as caught:
        load_config(tmp_path / "smelt.yml")

    assert caught.value.source is not None
    assert caught.value.source.endswith("base.yml")


def test_composition_never_supplies_missing_required_values(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "base.yml": "rules: {}\n",
            "smelt.yml": "extends: base.yml\nproject: {root_packages: [app]}\n",
        },
    )

    with pytest.raises(ConfigError, match=r"version: 1.*required"):
        load_config(tmp_path / "smelt.yml")


def test_recursive_yaml_aliases_are_configuration_errors(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: &loop {root_packages: [app], nested: *loop}\n"
        },
    )

    with pytest.raises(ConfigError, match="recursive YAML") as caught:
        load_config(tmp_path / "smelt.yml")

    assert caught.value.issues[0].path == "project.nested"


def test_deep_inheritance_is_bounded(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            f"policy{index}.yml": f"extends: policy{index + 1}.yml\n"
            for index in range(33)
        },
    )

    with pytest.raises(ConfigError, match="exceeds 32 files"):
        load_config(tmp_path / "policy0.yml")


def test_absolute_parent_paths_are_rejected(tmp_path: Path) -> None:
    write_project(
        tmp_path, {"smelt.yml": f"extends: '{(tmp_path / 'base.yml').as_posix()}'\n"}
    )

    with pytest.raises(ConfigError, match="local relative file path"):
        load_config(tmp_path / "smelt.yml")
