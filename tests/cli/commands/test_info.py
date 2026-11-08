"""Rule groups are discoverable through the existing catalog commands."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from smelt.cli import main

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
        assert "rules: {information-hiding: warning}" in output
        assert "SMT335 internal-public-attribute" in output


def test_catalog_exposes_group_membership_without_adding_fake_diagnostics(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["rules", "--format", "json"]) == 0
    rules = json.loads(capsys.readouterr().out)
    assert {
        rule["code"] for rule in rules if "information-hiding" in rule["groups"]
    } == {"SMT316", "SMT320", "SMT335", "SMT429", "SMT430"}
    assert all(rule["code"].startswith("SMT") for rule in rules)


def test_maintainability_init_writes_one_information_hiding_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").write_text("")
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--maintainability"]) == 0
    capsys.readouterr()
    config = (tmp_path / "smelt.yml").read_text()
    assert "information-hiding: warning" in config
    assert "internal-public-member:" not in config
    assert "test-only-public-function:" not in config
    assert "test-only-public-attribute:" not in config
