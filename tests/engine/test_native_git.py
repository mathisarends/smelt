from __future__ import annotations

import base64
import json
import os
import subprocess
import sysconfig
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from smelt.cli import main
from smelt.engine import native_git
from smelt.engine.changes import capture_index, capture_repository, read_blobs

if TYPE_CHECKING:
    from collections.abc import Iterator


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
        cwd=root,
        stderr=subprocess.PIPE,
    )


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    native_git.close()
    name = "smelt.exe" if os.name == "nt" else "smelt"
    executable = Path(sysconfig.get_path("scripts")) / name
    if not executable.is_file():
        pytest.skip("install the native client")
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("SMELT_NATIVE_CLIENT", str(executable))
    monkeypatch.delenv("SMELT_NATIVE_GIT", raising=False)
    monkeypatch.setattr(native_git, "_failed", False)
    with native_git.check_scope(enabled=True):
        yield
    native_git.close()


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
@pytest.mark.parametrize("index_version", ["2", "4"])
def test_native_index_tree_and_blobs_match_git_and_refresh_after_edits(
    tmp_path: Path, service: None, object_format: str, index_version: str
) -> None:
    git(tmp_path, "init", "-q", "-b", "main", f"--object-format={object_format}")
    assert native_git.capture(tmp_path) == (tmp_path.resolve(), None)
    assert native_git.index_records(tmp_path) == b""
    for name in ("plain.py", "dotted.name", "dotted/é space.py", "nested/deeper.py"):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(f"# {name}\n".encode())
    git(tmp_path, "add", ".")
    git(tmp_path, "update-index", f"--index-version={index_version}")
    git(tmp_path, "commit", "-qm", "initial")
    git(tmp_path, "gc", "--prune=now")
    for content in (b"# first edit\n", b"# other edit\n"):
        (tmp_path / "plain.py").write_bytes(content)
        git(tmp_path, "add", "plain.py")
        with native_git.check_scope(enabled=True):
            captured = native_git.capture(tmp_path)
            assert captured is not None
            root, head = captured
            assert root == tmp_path.resolve()
            assert head == git(tmp_path, "rev-parse", "HEAD").decode().strip()
            records = native_git.index_records(root)
            assert records == git(tmp_path, "ls-files", "--stage", "-z")
            assert native_git.tree_records(root, str(head)) == git(
                tmp_path, "ls-tree", "-rz", "--full-tree", str(head)
            )
            index = capture_index(root)
            blobs = native_git.read_blobs(root, list(set(index.blobs.values())))
            assert blobs is not None
            assert blobs[index.blobs["plain.py"]] == content
            for oid, data in blobs.items():
                assert data == git(tmp_path, "cat-file", "blob", oid)


def test_native_capture_uses_linked_worktree_head_and_index(
    tmp_path: Path, service: None
) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("# main\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "initial")
    worktree = tmp_path / "linked"
    git(tmp_path, "worktree", "add", "-qb", "linked", str(worktree))
    (worktree / "a.py").write_text("# linked\n")
    git(worktree, "add", "a.py")
    captured = native_git.capture(worktree)
    assert captured is not None
    assert captured[0] == worktree.resolve()
    assert native_git.index_records(worktree) == git(
        worktree, "ls-files", "--stage", "-z"
    )


def test_native_failure_and_git_environment_use_independent_git_fallback(
    tmp_path: Path, service: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("# source\n")
    git(tmp_path, "add", ".")
    assert native_git.capture(tmp_path) is not None
    with native_git.check_scope(enabled=False):
        expected = capture_index(tmp_path)
    with monkeypatch.context() as patch:
        patch.setenv("GIT_INDEX_FILE", str(tmp_path / ".git/index"))
        assert native_git.capture(tmp_path) is None
        assert capture_index(tmp_path) == expected
    native_git._service.process.kill()  # type: ignore[union-attr]
    with native_git.check_scope(enabled=True):
        assert capture_repository(tmp_path).path == tmp_path.resolve()
        assert capture_index(tmp_path) == expected
        assert read_blobs(tmp_path, list(expected.blobs.values()))


def test_split_index_never_produces_an_incomplete_native_manifest(
    tmp_path: Path, service: None
) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text(f"# {name}\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "update-index", "--split-index")
    (tmp_path / "b.py").write_text("# changed\n")
    git(tmp_path, "add", "b.py")
    response = native_git.request("index", tmp_path)
    if response is not None:
        assert base64.b64decode(response["records"]) == git(
            tmp_path, "ls-files", "--stage", "-z"
        )
    with native_git.check_scope(enabled=True):
        index = capture_index(tmp_path)
    with native_git.check_scope(enabled=False):
        assert index == capture_index(tmp_path)


def test_complete_staged_check_needs_no_git_process_with_native_service(
    tmp_path: Path,
    service: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "smelt.yaml").write_text(
        "version: 1\nproject: {root_packages: [app]}\n"
        "structure:\n  contracts:\n    api:\n      match: app\n"
        "      rules: {internal-public-member: warning}\n"
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").write_text("")
    (tmp_path / "app/api.py").write_text("class Api:\n    def emit(self): pass\n")
    (tmp_path / "app/caller.py").write_text("from .api import Api\nApi().emit()\n")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "initial")
    (tmp_path / "app/caller.py").write_text("# removed last caller\n")
    git(tmp_path, "add", "app/caller.py")
    monkeypatch.chdir(tmp_path)
    args = ["check", "--staged", "--format", "json", "--fail-on", "warning"]
    expected_code = main([*args, "--no-cache"])
    expected = json.loads(capsys.readouterr().out)

    def no_git(*args: object, **kwargs: object) -> None:
        pytest.fail("a standard staged check must use the native Git reader")

    monkeypatch.setattr(subprocess, "run", no_git)
    assert main(args) == expected_code == 1
    assert json.loads(capsys.readouterr().out) == expected
