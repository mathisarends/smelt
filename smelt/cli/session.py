"""Bounded local workers for the native CLI; each request captures fresh inputs."""

from __future__ import annotations

import contextlib
import io
import json
import os
import secrets
import socket
import sys
from pathlib import Path
from typing import Any

from smelt.analysis.identity import analyzer_identity
from smelt.cli.app import main

_LIMIT = 1024 * 1024
_PROTOCOL = 1


class Capture(io.StringIO):
    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def dispatch(message: dict[str, Any], root: str, identity: str) -> dict[str, Any]:
    analyzer_identity.cache_clear()
    if analyzer_identity() != identity:
        return {"code": 2, "out": "", "err": "", "restart": True}
    arguments = message.get("arguments")
    if (
        message.get("cwd") != root
        or not isinstance(arguments, list)
        or not arguments
        or arguments[0] != "check"
        or any(not isinstance(value, str) for value in arguments)
        or any(
            value.startswith("--fi") or value in {"--f", "--help", "-h"}
            for value in arguments
        )
    ):
        raise ValueError("worker accepts read-only checks for its original directory")
    out = Capture(tty=message.get("tty") is True)
    err = Capture(tty=False)
    with (
        contextlib.chdir(root),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        code = main(arguments)
    return {"code": code, "out": out.getvalue(), "err": err.getvalue()}


def serve(
    endpoint: Path, *, root: str | None = None, idle_seconds: float = 120
) -> None:
    identity = analyzer_identity()
    root = root or os.getcwd()  # noqa: PTH109 - match the native client's lexical cwd
    # Neither the Python worker nor its Windows environment redirector may keep
    # an idle project's directory open and prevent worktree deletion or renames.
    os.chdir(endpoint.parent)
    token = secrets.token_hex(32)
    with socket.create_server(("127.0.0.1", 0), backlog=32) as listener:
        listener.settimeout(idle_seconds)
        metadata = {
            "port": listener.getsockname()[1],
            "token": token,
            "pid": os.getpid(),
            "protocol": _PROTOCOL,
        }
        pending = endpoint.with_suffix(f".{os.getpid()}.pending")
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(metadata, output)
        pending.replace(endpoint)
        try:
            while True:
                try:
                    stream, _ = listener.accept()
                except TimeoutError:
                    break
                with stream:
                    stream.settimeout(120)
                    try:
                        with stream.makefile("rb") as incoming:
                            line = incoming.readline(_LIMIT)
                        message = json.loads(line)
                        if (
                            not isinstance(message, dict)
                            or message.get("token") != token
                        ):
                            raise ValueError("invalid worker token")
                        response = (
                            {"code": 0, "out": "", "err": "", "restart": True}
                            if message.get("arguments") == ["shutdown"]
                            else dispatch(message, root, identity)
                        )
                    except (ValueError, KeyError, TypeError, OSError):
                        # Invalid transport metadata restarts or falls back to
                        # checking; it must not become the check's verdict.
                        response = {"code": 2, "out": "", "err": "", "restart": True}
                    response["pid"] = os.getpid()
                    with contextlib.suppress(OSError):
                        stream.sendall(json.dumps(response).encode() + b"\n")
                    if response.get("restart"):
                        break
        finally:
            with contextlib.suppress(OSError, ValueError):
                if json.loads(endpoint.read_text(encoding="utf-8"))["token"] == token:
                    endpoint.unlink()


if __name__ == "__main__":
    if client := sys.argv[3:]:
        os.environ["SMELT_NATIVE_CLIENT"] = client[0]
    serve(Path(sys.argv[1]), root=sys.argv[2])
