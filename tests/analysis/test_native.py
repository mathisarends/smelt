from __future__ import annotations

from typing import TYPE_CHECKING

from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

PROJECT = {
    "smelt.yml": """
        version: 2
        project:
          root_packages: [shop]
        style:
          forbidden_names: [utils]
        rules:
          layout/forbidden-module-name: error
    """,
    "shop/__init__.py": "",
    "shop/orders.py": "ORDERS = 1\n",
}


def test_profiled_check_reports_core_phases_to_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_project(tmp_path, PROJECT)
    monkeypatch.setenv("SMELT_PROFILE", "1")

    check(tmp_path)

    phases = capsys.readouterr().err.splitlines()
    assert any(line.endswith(" discover files") for line in phases)
    assert any(line.endswith(" rule LAY001") for line in phases)
    assert phases[-1].startswith("smelt profile: --internal-check took ")


def test_unprofiled_check_keeps_stderr_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_project(tmp_path, PROJECT)

    check(tmp_path)

    assert capsys.readouterr().err == ""
