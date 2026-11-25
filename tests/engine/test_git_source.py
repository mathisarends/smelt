from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from smelt.analysis.files import FileIndex
from smelt.config.models import SmeltConfig
from smelt.engine.git_source import GitSourceStore
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

CONFIG = SmeltConfig.model_validate(
    {"version": 2, "project": {"root_packages": ["app"]}}
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
        timeout=10,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write_project(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/service.py": "VALUE = 1\n",
            "scripts/tool.py": "import app\n",
            "tests/test_service.py": "from app.service import VALUE\n",
        },
    )
    # A BOM and Windows line endings decode to the text a disk check reads.
    (tmp_path / "app/windows.py").write_bytes(b"\xef\xbb\xbfVALUE = 2\r\n")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


def test_disk_and_git_views_read_the_same_text(repo: Path) -> None:
    disk = FileIndex.discover(repo, CONFIG)
    committed = FileIndex.discover(
        repo, CONFIG, store=GitSourceStore.from_revision(repo, "HEAD")
    )
    paths = sorted(disk.all_python_paths())

    assert paths == sorted(committed.all_python_paths())
    assert [committed.read_text(path) for path in paths] == [
        disk.read_text(path) for path in paths
    ]


def test_path_outside_the_snapshot_has_no_identity(repo: Path) -> None:
    store = GitSourceStore.from_revision(repo, "HEAD")

    assert store.identity(repo / "app/missing.py") is None
