"""Rule discovery and explicit starter configuration."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from smelt.cli import main
from tests.helpers import write_policy_project

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_removed_information_hiding_selector_has_a_migration_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["explain", "information-hiding"]) == 2
    assert (
        "List visibility/internal-public-member, "
        "visibility/internal-public-module-symbol, "
        "visibility/internal-public-attribute, "
        "visibility/test-only-public-function, "
        "visibility/test-only-public-attribute individually" in capsys.readouterr().err
    )


def test_maintainability_init_lists_individual_rules_and_optional_hints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").write_text("")
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--maintainability"]) == 0
    capsys.readouterr()
    config = (tmp_path / "smelt.yml").read_text()
    assert "information-hiding: warning" not in config
    assert "visibility/internal-public-member: warning" in config
    assert "visibility/test-only-public-function: warning" in config
    assert "visibility/test-only-public-attribute: warning" in config
    assert "# typing/duplicate-literal-type: hint" in config


def test_unconfigured_rules_skip_named_decisions_and_policy_activation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_policy_project(
        tmp_path,
        {
            "app/__init__.py": "",
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n"
            "style: {module_import_exemptions: []}\n"
            "rules: {visibility/internal-public-member: off}\n",
        },
    )

    assert (
        main(
            [
                "rules",
                "--unconfigured",
                "--format",
                "json",
                "--config",
                str(root / "smelt.yml"),
            ]
        )
        == 0
    )
    found = {rule["code"] for rule in json.loads(capsys.readouterr().out)}
    assert "VIS001" not in found
    assert "IMP002" not in found
    assert "TYP007" in found


def test_rule_listing_explains_its_columns_without_a_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["rules"]) == 0

    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == ["code", "rule", "status", "severity"]
    assert "status without a smelt.yml:" in out
    assert "needs policy: declare its policy before enabling it" in out


def test_rule_listing_reports_the_status_in_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_policy_project(
        tmp_path,
        {
            "app/__init__.py": "",
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n"
            "rules: {complexity/excess-positional-parameters: error}\n",
        },
    )
    monkeypatch.chdir(tmp_path)

    assert main(["rules", "--format", "json"]) == 0

    listed = {rule["code"]: rule for rule in json.loads(capsys.readouterr().out)}
    assert (listed["CPX003"]["status"], listed["CPX003"]["severity"]) == ("on", "error")
    assert (listed["TYP007"]["status"], listed["TYP007"]["severity"]) == ("off", None)


def test_explain_shows_how_to_enable_and_the_project_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_policy_project(
        tmp_path,
        {
            "app/__init__.py": "",
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n",
        },
    )
    monkeypatch.chdir(tmp_path)

    assert main(["explain", "architecture/layer-boundary"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[3:7] == [
        "Enable:    rules: {architecture/layer-boundary: error}",
        "Options:   architecture.layers.<layer>.may_depend_on, "
        "architecture.layers.<layer>.modules, architecture.import_graph.transitive, "
        "architecture.import_graph.type_checking",
        "Requires:  architecture.layers defines layer dependencies.",
        "Status:    needs policy in smelt.yml",
    ]


def test_explain_json_omits_the_status_outside_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["explain", "CPX003", "--format", "json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["status"] is None
    assert data["options"] == ["max (default 5)", "style.overrides"]
    assert data["requires_policy"] is None


def test_explain_states_when_a_finding_is_a_smell_and_how_to_list_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["explain", "MOD013"]) == 0

    lines = capsys.readouterr().out.splitlines()
    brief = [line for line in lines if line.startswith(("Smell:", "No smell:", "Fix:"))]
    assert [line.split(":")[0] for line in brief] == ["Smell", "No smell", "Fix"]
    assert "Findings:  smelt check --select MOD013" in lines
    assert main(["explain", "MOD013", "--format", "json"]) == 0
    explained = json.loads(capsys.readouterr().out)
    assert explained["no_smell"].startswith("A published name")
    assert explained["repair"].startswith("Rename to the suggested name")
