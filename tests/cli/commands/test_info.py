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
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\n"
            "style: {max_positional_parameters: 3}\n"
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


def test_rule_listing_explains_its_columns(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["rules"]) == 0

    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == ["code", "rule", "enabled", "severity"]
    assert "policy:" not in out
    assert "explicit: runs only when listed under rules: in smelt.yml" in out
