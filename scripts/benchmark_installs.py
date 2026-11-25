"""Disposable installations of Smelt revisions for the benchmark scripts."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        [  # noqa: S607 - use Git from PATH, like the hooks under measurement
            "git",
            "-c",
            "user.name=benchmark",
            "-c",
            "user.email=benchmark@example.invalid",
            *args,
        ],
        cwd=root,
        capture_output=True,
        check=True,
    )


def command(root: Path, args: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed benchmark commands
        args,
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = completed.stdout.decode(errors="replace")
    if completed.returncode:
        raise RuntimeError(f"{args} failed in {root}:\n{output}")
    return output.strip()


def isolated_env(repo: Path) -> dict[str, str]:
    """The environment for installations that share this checkout's tool caches."""
    env = {
        **os.environ,
        "UV_CACHE_DIR": command(repo, ["uv", "cache", "dir"]),
        "UV_PYTHON_INSTALL_DIR": command(repo, ["uv", "python", "dir"]),
        "PREK_HOME": command(repo, ["uv", "run", "--frozen", "prek", "cache", "dir"]),
    }
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    env.pop("VIRTUAL_ENV", None)
    return env


def install(
    repo: Path, revisions: dict[str, str], *, parent: Path, env: dict[str, str]
) -> dict[str, Path]:
    """A synced clone of `repo` at each named revision, below `parent`."""
    roots = {name: parent / name for name in revisions}
    for name, revision in revisions.items():
        sha = command(repo, ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"])
        git(
            repo,
            "clone",
            "--quiet",
            "--shared",
            "--no-checkout",
            str(repo),
            str(roots[name]),
        )
        git(roots[name], "checkout", "--quiet", "--detach", sha)
        command(roots[name], ["uv", "sync", "--frozen"], env=env)
        sys.stdout.write(f"{name}: {sha}\n")
        sys.stdout.flush()
    return roots
