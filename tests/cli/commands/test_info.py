"""Rule groups are discoverable through the existing catalog commands."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from smelt.cli import main
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_explain_information_hiding(
    capsys: pytest.CaptureFixture[str], output_format: str
) -> None:
    assert main(["explain", "information-hiding", "--format", output_format]) == 0
    output = capsys.readouterr().out
    if output_format == "json":
        group = json.loads(output)
        assert group["name"] == "information-hiding"
        assert [rule["code"] for rule in group["rules"]] == [
            "SMT316",
            "SMT320",
            "SMT335",
            "SMT429",
            "SMT430",
        ]
    else:
        assert "--select information-hiding" in output
        assert "SMT335 visibility/internal-public-attribute" in output


def test_catalog_exposes_group_membership_without_adding_fake_diagnostics(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["rules", "--format", "json"]) == 0
    rules = json.loads(capsys.readouterr().out)
    assert {
        rule["code"] for rule in rules if "information-hiding" in rule["groups"]
    } == {"SMT316", "SMT320", "SMT335", "SMT429", "SMT430"}
    assert all(rule["code"].startswith("SMT") for rule in rules)


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
    root = write_project(
        tmp_path,
        {
            "app/__init__.py": "",
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\n"
            "structure: {max_positional_parameters: 3}\n"
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
    assert "SMT316" not in found
    assert "SMT345" not in found
    assert "SMT347" in found
