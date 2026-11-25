from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from smelt.cli import main
from smelt.config import load_config
from smelt.engine.check import CheckOptions
from tests.helpers import FIXTURES, check, write_project

if TYPE_CHECKING:
    from pathlib import Path


def _workspace(tmp_path: Path) -> Path:
    return shutil.copytree(FIXTURES / "uv_workspace", tmp_path, dirs_exist_ok=True)


def test_workspace_fixture_keeps_one_graph_with_member_architectures(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    outcome = check(root)

    assert outcome.report.violations == []
    assert set(outcome.context.model.part_for("core.domain.task").layers) == {
        "domain",
        "infrastructure",
    }
    assert set(
        outcome.context.model.part_for("api.features.orders.application.service").layers
    ) == {"domain", "application"}
    visibility = check(root, CheckOptions(select=("visibility",))).report.violations
    assert not any(finding.member == "packages/core" for finding in visibility)


def test_init_writes_shared_root_and_independent_member_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path)
    for name in ("smelt.yml", "services/api/smelt.yml", "packages/core/smelt.yml"):
        (root / name).unlink()
    monkeypatch.chdir(root)

    assert main(["init", "--maintainability"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert "independent drafts" in output.out
    config = load_config(root / "smelt.yml").config
    assert config.project.workspace == "uv"
    draft = (root / "smelt.yml").read_text(encoding="utf-8")
    assert "#   members:" in draft
    assert "#     packages/*:" in draft
    assert "#     services/*:" in draft
    assert "#   services/api -> packages/core" in draft
    assert "#       - packages/core" in draft
    assert config.architecture.members == {}
    assert set(config.architecture.layers) == set()
    api = config.members["services/api"].architecture
    core = config.members["packages/core"].architecture
    assert api is not None
    assert core is not None
    assert set(api.layers) == {
        "domain",
        "application",
    }
    assert set(core.layers) == {
        "domain",
        "infrastructure",
    }
    assert "layout/definition-order" in config.rules
    # Member layers are checked by the root rule that the draft names.
    assert config.rules["architecture/layer-boundary"] == "error"
    assert "rules:" not in (root / "services/api/smelt.yml").read_text(encoding="utf-8")
    write_project(root, {"packages/new/src/new/__init__.py": ""})
    assert "new" in load_config(root / "smelt.yml").config.project.root_packages


def test_member_init_preserves_root_and_other_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path)
    before = {
        path: (root / path).read_bytes()
        for path in ("smelt.yml", "packages/core/smelt.yml")
    }
    (root / "services/api/smelt.yml").unlink()
    monkeypatch.chdir(root)

    assert main(["init", "--member", "services/api"]) == 0
    assert capsys.readouterr().err == ""
    assert all(
        (root / path).read_bytes() == content for path, content in before.items()
    )
    architecture = (
        load_config(root / "smelt.yml").config.members["services/api"].architecture
    )
    assert architecture is not None
    assert "application" in architecture.layers


@pytest.mark.parametrize("member_only", [False, True])
def test_flat_member_draft_leaves_local_layout_to_the_member(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    member_only: bool,
) -> None:
    root = write_project(
        tmp_path,
        {
            "pyproject.toml": '[tool.uv.workspace]\nmembers = ["libs/*"]\n',
            "libs/core/src/core/__init__.py": "",
            "libs/core/src/core/ids.py": "VALUE = 1\n",
        },
    )
    shared = "version: 2\nproject: {workspace: uv}\nrules: {architecture/uncovered-source: error}\n"
    if member_only:
        write_project(root, {"smelt.yml": shared})
    monkeypatch.chdir(root)

    assert main(["init", *(["--member", "libs/core"] if member_only else [])]) == 0
    assert "architecture:" not in (root / "libs/core/smelt.yml").read_text(
        encoding="utf-8"
    )
    if not member_only:
        write_project(root, {"smelt.yml": shared})
    assert {finding.code for finding in check(root).report.violations} == {"ARC013"}
    write_project(root, {"libs/core/smelt.yml": "architecture: {shared: [core]}\n"})
    assert check(root).report.violations == []


def test_init_checks_all_outputs_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _workspace(tmp_path)
    (root / "smelt.yml").unlink()
    before = (root / "services/api/smelt.yml").read_bytes()
    monkeypatch.chdir(root)

    assert main(["init"]) == 2
    assert "already exists" in capsys.readouterr().err
    assert not (root / "smelt.yml").exists()
    assert not (root / ".smelt.schema.json").exists()
    assert (root / "services/api/smelt.yml").read_bytes() == before


@pytest.mark.parametrize("clash", [False, True])
def test_workspace_init_drafts_mirror_and_defers_incomplete_layers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    clash: bool,
) -> None:
    second = "sync" if clash else "launch"
    root = write_project(
        tmp_path,
        {
            "pyproject.toml": '[tool.uv.workspace]\nmembers = ["apps/*"]\n',
            "apps/worker/src/worker/__init__.py": "",
            "apps/worker/src/worker/adapters/queue.py": "",
            "apps/worker/src/worker/jobs/sync.py": "",
            "apps/worker/tests/jobs/test_sync.py": "",
            "apps/runner/src/runner/__init__.py": "",
            f"apps/runner/src/runner/adapters/{second}.py": "",
            f"apps/runner/tests/adapters/test_{second}.py": "",
        },
    )
    monkeypatch.chdir(root)

    assert main(["init"]) == 0
    output = capsys.readouterr().out
    config = load_config(root / "smelt.yml").config
    assert config.tests.layout == ("none" if clash else "mirror")
    assert ("mirror not enabled because pytest" in output) is clash
    if clash:
        assert "apps/worker/tests/jobs/test_sync.py" in output
        assert "apps/runner/tests/adapters/test_sync.py" in output
    assert config.members["apps/worker"].architecture is None
    assert "worker.jobs" in (root / "apps/worker/smelt.yml").read_text(encoding="utf-8")
    assert "ARC011" not in {v.code for v in check(root).report.violations}


@pytest.mark.parametrize("member", ["../outside", "services/missing", "packages"])
def test_member_init_rejects_undeclared_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    member: str,
) -> None:
    root = _workspace(tmp_path)
    monkeypatch.chdir(root)

    assert main(["init", "--member", member]) == 2
    assert "not a declared" in capsys.readouterr().err
