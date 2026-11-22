from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from smelt.analysis.cache import FactCache
from smelt.analysis.files import FileIndex
from smelt.analysis.import_cache import resolution_identity
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


def test_disk_and_git_views_identify_the_same_text_alike(repo: Path) -> None:
    disk = FileIndex.discover(repo, CONFIG)
    committed = FileIndex.discover(
        repo, CONFIG, store=GitSourceStore.from_revision(repo, "HEAD")
    )
    paths = sorted(disk.all_python_paths())

    assert paths == sorted(committed.all_python_paths())
    assert [committed.content_id(path) for path in paths] == [
        disk.content_id(path) for path in paths
    ]


def test_disk_and_git_views_share_the_import_resolution_identity(repo: Path) -> None:
    disk = FileIndex.discover(repo, CONFIG)
    committed = FileIndex.discover(
        repo, CONFIG, store=GitSourceStore.from_revision(repo, "HEAD")
    )

    assert resolution_identity(committed) == resolution_identity(disk)


def test_cached_text_digest_needs_no_blob_contents(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repo / "app/service.py"
    first = FactCache(repo / ".smelt_cache")
    expected = GitSourceStore.from_revision(repo, "HEAD", cache=first).content_digest(
        path
    )
    first.close()

    def read_bytes(store: GitSourceStore, path: Path) -> bytes:
        pytest.fail(f"a cached digest must not read {path}")

    monkeypatch.setattr(GitSourceStore, "read_bytes", read_bytes)
    second = FactCache(repo / ".smelt_cache")
    digest = GitSourceStore.from_revision(repo, "HEAD", cache=second).content_digest(
        path
    )
    second.close()

    assert digest == expected


def test_undecodable_blob_keeps_its_object_identity(repo: Path) -> None:
    (repo / "app/binary.py").write_bytes(b"\xff\xfe\x00")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "undecodable")
    store = GitSourceStore.from_revision(repo, "HEAD")

    digest = store.content_digest(repo / "app/binary.py")

    assert digest == store.identity(repo / "app/binary.py")


def test_path_outside_the_snapshot_has_no_digest(repo: Path) -> None:
    store = GitSourceStore.from_revision(repo, "HEAD")

    assert store.content_digest(repo / "app/missing.py") is None
