"""Process and worker helpers for local performance measurements."""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse
    from pathlib import Path


def run(command: list[str], root: Path, **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - explicit maintainer commands, no shell
        command, cwd=root, capture_output=True, check=True, **kwargs
    )


def shutdown(sessions: Path) -> None:
    for endpoint in sessions.glob("smelt/sessions-v1/*.json"):
        try:
            metadata = json.loads(endpoint.read_text())
            with socket.create_connection(("127.0.0.1", metadata["port"]), 1) as stream:
                stream.sendall(
                    json.dumps(
                        {"token": metadata["token"], "arguments": ["shutdown"]}
                    ).encode()
                    + b"\n"
                )
                stream.recv(4096)
        except (OSError, ValueError, KeyError):
            continue


def invoke(
    root: Path,
    source: Path,
    args: argparse.Namespace,
    flags: list[str],
    *,
    mode: str = "native",
) -> tuple[float, dict[str, Any]]:
    env = {
        **os.environ,
        "PYTHONPATH": str(source),
        "SMELT_PYTHON": sys.executable,
        "SMELT_DAEMON": "0" if mode == "foreground" else "1",
        "LOCALAPPDATA": str(args.sessions),
        "XDG_CACHE_HOME": str(args.sessions),
        "PYTHONIOENCODING": "utf-8",
    }
    command = [str(args.native)]
    if mode == "python":
        command = [sys.executable, "-P", "-m", "smelt"]
        env.pop("SMELT_NATIVE_CLIENT", None)
    check = ["check", *flags, "--format", "json", "--fail-on", "warning"]
    if mode == "hook":
        # Windows hook arguments are parsed by shlex too; forward slashes avoid
        # backslashes being interpreted as escapes in an unquoted executable.
        entry = shlex.join([str(args.native).replace("\\", "/"), *check])
        config = root / ".benchmark-hooks.yml"
        config.write_text(
            "repos:\n- repo: local\n  hooks:\n  - id: smelt\n    name: smelt\n"
            f"    entry: {json.dumps(entry)}\n    language: system\n"
            "    pass_filenames: false\n    always_run: true\n"
        )
        command = [str(args.runner), "run", "-c", str(config), "smelt", "--verbose"]
    else:
        command += check
    started = time.perf_counter()
    result = subprocess.run(  # noqa: S603 - explicit benchmark commands
        command, cwd=root, env=env, capture_output=True, timeout=180, check=False
    )
    elapsed = time.perf_counter() - started
    output = result.stdout.decode("utf-8")
    if result.returncode not in {0, 1} or result.stderr:
        raise RuntimeError(f"{command}: {result.stderr.decode()}\n{output}")
    report, _ = json.JSONDecoder().raw_decode(output[output.index("{") :])
    if report.get("status") not in {"passed", "failed"}:
        raise RuntimeError(f"Unexpected report: {report}")
    return elapsed, report
