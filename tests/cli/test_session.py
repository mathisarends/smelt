from __future__ import annotations

import concurrent.futures
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import smelt
from tests.helpers import write_project

if TYPE_CHECKING:
    from collections.abc import Iterator

CONFIG = """version: 1
project: {root_packages: [app]}
structure:
  contracts:
    api: {match: app, rules: {internal-public-member: warning}}
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=test", "-c", "user.email=t@t", *args],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=True,
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "project"
    write_project(
        root,
        {
            "smelt.yaml": CONFIG,
            ".gitignore": ".smelt_cache/\n",
            "app/__init__.py": "",
            "app/browser.py": "class Browser:\n    def emit(self): pass\n",
            "app/caller.py": "from .browser import Browser\nBrowser().emit()\n",
        },
    )
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "initial")
    cache = tmp_path / "sessions"
    monkeypatch.setenv("LOCALAPPDATA", str(cache))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.setenv("SMELT_PYTHON", sys.executable)
    monkeypatch.setenv("SMELT_DAEMON", "1")
    yield root
    for endpoint in cache.rglob("*.json"):
        with contextlib.suppress(OSError, ValueError):
            _request(endpoint, root, ["shutdown"])


@pytest.fixture
def native() -> Path:
    name = "smelt.exe" if os.name == "nt" else "smelt"
    path = Path(sysconfig.get_path("scripts")) / name
    if not path.is_file():
        pytest.skip("install the package to exercise its native CLI")
    return path


def _invoke(
    command: list[str], root: Path, *arguments: str
) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(  # noqa: S603
        [
            *command,
            "check",
            "--staged",
            "--format",
            "json",
            "--fail-on",
            "warning",
            *arguments,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert not completed.stderr
    return completed.returncode, json.loads(completed.stdout)


def _fresh(root: Path) -> tuple[int, dict[str, Any]]:
    return _invoke([sys.executable, "-P", "-m", "smelt"], root, "--no-cache")


def _endpoint(project: Path) -> Path:
    [endpoint] = list((project.parent / "sessions").rglob("*.json"))
    return endpoint


def _pid(project: Path) -> int:
    return int(json.loads(_endpoint(project).read_text())["pid"])


def _request(endpoint: Path, root: Path, arguments: list[str]) -> dict[str, Any]:
    metadata = json.loads(endpoint.read_text())
    with socket.create_connection(("127.0.0.1", metadata["port"]), timeout=5) as stream:
        stream.sendall(
            json.dumps(
                {"token": metadata["token"], "cwd": str(root), "arguments": arguments}
            ).encode()
            + b"\n"
        )
        return json.loads(stream.makefile("rb").readline())  # type: ignore[no-any-return]


def test_native_clients_preserve_partial_staging_and_reuse_worker(
    project: Path, native: Path
) -> None:
    assert _invoke([str(native)], project) == _fresh(project)
    pid = _pid(project)
    (project / "app/caller.py").write_text("")
    _git(project, "add", "app/caller.py")
    (project / "app/caller.py").write_text(
        "from .browser import Browser\nBrowser().emit()\n"
    )
    actual = _invoke([str(native)], project)
    assert actual == _fresh(project)
    assert actual[0] == 1
    assert _pid(project) == pid
    _git(project, "commit", "-qm", "known finding")
    assert _invoke([str(native)], project) == _fresh(project)
    assert _pid(project) == pid
    (project / "app/new.py").write_text(
        "class New:\n" + "".join(f"    def method_{i}(self): pass\n" for i in range(10))
    )
    _git(project, "add", "app/new.py")
    actual = _invoke([str(native)], project)
    assert actual == _fresh(project)
    assert len(json.dumps(actual)) > 4096  # complete large piped reports
    assert _pid(project) == pid


def test_native_worker_recovers_after_invalid_staged_config(
    project: Path, native: Path
) -> None:
    _invoke([str(native)], project)
    pid = _pid(project)
    config = project / "smelt.yaml"
    config.write_text("invalid: [")
    _git(project, "add", "smelt.yaml")
    actual = _invoke([str(native)], project)
    assert actual == _fresh(project)
    assert actual[0] == 2
    config.write_text(CONFIG)
    _git(project, "add", "smelt.yaml")
    assert _invoke([str(native)], project) == _fresh(project)
    assert _pid(project) == pid


def test_native_worker_restarts_after_analyzer_changes(
    project: Path, native: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = project.parent / "analyzer"
    package = source / "smelt"
    shutil.copytree(
        Path(smelt.__file__).parent,
        package,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    monkeypatch.setenv("PYTHONPATH", str(source))
    _invoke([str(native)], project)
    pid = _pid(project)
    with (package / "__init__.py").open("a") as output:
        output.write("\n# Changed analyzer build.\n")
    assert _invoke([str(native)], project) == _fresh(project)
    assert _pid(project) != pid


def test_native_client_recovers_after_worker_is_killed(
    project: Path, native: Path
) -> None:
    _invoke([str(native)], project)
    pid = _pid(project)
    os.kill(pid, signal.SIGTERM)
    assert _invoke([str(native)], project) == _fresh(project)
    assert _pid(project) != pid


def test_concurrent_first_clients_preserve_reports(project: Path, native: Path) -> None:
    expected = _fresh(project)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: _invoke([str(native)], project), range(4)))
    assert all(result == expected for result in results)
    _endpoint(project)


def test_native_ci_path_uses_foreground_by_default(
    project: Path, native: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SMELT_DAEMON")
    monkeypatch.setenv("CI", "true")
    assert _invoke([str(native)], project) == _fresh(project)
    assert not list((project.parent / "sessions").rglob("*.json"))


def test_native_client_falls_back_when_session_directory_is_unwritable(
    project: Path, native: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked = project.parent / "not-a-directory"
    blocked.write_text("occupied")
    monkeypatch.setenv("LOCALAPPDATA", str(blocked))
    monkeypatch.setenv("XDG_CACHE_HOME", str(blocked))
    assert _invoke([str(native)], project) == _fresh(project)


def test_idle_worker_does_not_pin_project_directory(
    project: Path, native: Path
) -> None:
    _invoke([str(native)], project)
    moved = project.with_name("moved")
    project.rename(moved)
    assert moved.is_dir()


def test_native_client_recovers_after_endpoint_token_is_corrupted(
    project: Path, native: Path
) -> None:
    _invoke([str(native)], project)
    endpoint = _endpoint(project)
    metadata = json.loads(endpoint.read_text())
    metadata["token"] = metadata["token"][::-1]
    endpoint.write_text(json.dumps(metadata))
    assert _invoke([str(native)], project) == _fresh(project)
    assert _pid(project) != metadata["pid"]


def test_copied_native_client_can_find_interpreter_on_path(
    project: Path, native: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = project.parent / "client"
    directory.mkdir()
    binary = directory / native.name
    shutil.copy2(native, binary)
    monkeypatch.delenv("SMELT_PYTHON")
    monkeypatch.setenv(
        "PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
    )
    assert _invoke([str(binary)], project) == _fresh(project)
    _endpoint(project)


@pytest.mark.parametrize("mode", ["0", "1"])
def test_project_package_cannot_shadow_installed_analyzer(
    project: Path, native: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    shadow = project / "smelt"
    shadow.mkdir()
    (shadow / "__init__.py").write_text(
        "raise RuntimeError('analyzed project code must not execute')\n"
    )
    monkeypatch.setenv("SMELT_DAEMON", mode)
    assert _invoke([str(native)], project) == _fresh(project)


def test_relative_pythonpath_keeps_foreground_semantics(
    project: Path, native: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", ".")
    assert _invoke([str(native)], project) == _fresh(project)
    assert not list((project.parent / "sessions").rglob("*.json"))
