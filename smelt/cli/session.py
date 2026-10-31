"""Bounded local workers for the native CLI; each request captures fresh inputs."""

from __future__ import annotations

import contextlib
import io
import json
import os
import secrets
import socket
import sys
import threading
from pathlib import Path
from typing import Any

from smelt.analysis.identity import analyzer_identity
from smelt.analysis.retained import RetainedAnalysis, retain
from smelt.cli.app import main
from smelt.cli.session_slots import worker_slot

_LIMIT = 1024 * 1024
_PROTOCOL = 2
_REQUEST_SECONDS = 5


class Capture(io.StringIO):
    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _dispatch(
    message: dict[str, Any],
    root: str,
    identity: str,
    *,
    state: RetainedAnalysis | None = None,
) -> dict[str, Any]:
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
        retain(state),
        contextlib.chdir(root),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        code = main(arguments)
    return {"code": code, "out": out.getvalue(), "err": err.getvalue()}


def _serve_internal(
    endpoint: Path, *, root: str | None = None, idle_seconds: float = 120
) -> None:
    with worker_slot(endpoint.parent) as available:
        if available:
            _serve(endpoint, root=root, idle_seconds=idle_seconds)


def _serve(endpoint: Path, *, root: str | None, idle_seconds: float) -> None:
    identity = analyzer_identity()
    state = RetainedAnalysis()
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
                    stream.settimeout(0.25)
                    stream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    # This process owns the timeout. No PID from a stale endpoint
                    # is killed by a client, and OS locks/SQLite recover on exit.
                    watchdog = threading.Timer(_REQUEST_SECONDS, os._exit, args=(2,))
                    watchdog.daemon = True
                    watchdog.start()
                    try:
                        try:
                            response = _respond(stream, token, root, identity, state)
                        except (ValueError, KeyError, TypeError, OSError):
                            # Transport failure is never the check's verdict.
                            response = {
                                "code": 2,
                                "out": "",
                                "err": "",
                                "restart": True,
                            }
                        response["pid"] = os.getpid()
                        with contextlib.suppress(OSError):
                            stream.sendall(json.dumps(response).encode() + b"\n")
                    finally:
                        watchdog.cancel()
                        watchdog.join()
                    if response.get("restart"):
                        break
        finally:
            with contextlib.suppress(OSError, ValueError):
                if json.loads(endpoint.read_text(encoding="utf-8"))["token"] == token:
                    endpoint.unlink()


def _respond(
    stream: socket.socket,
    token: str,
    root: str,
    identity: str,
    state: RetainedAnalysis,
) -> dict[str, Any]:
    with stream.makefile("rb") as incoming:
        line = incoming.readline(_LIMIT)
    message = json.loads(line)
    if not isinstance(message, dict) or message.get("token") != token:
        raise ValueError("invalid worker token")
    stream.settimeout(_REQUEST_SECONDS)
    if message.get("ack") is True:
        stream.sendall(
            json.dumps({"accepted": True, "pid": os.getpid()}).encode() + b"\n"
        )
    if message.get("arguments") == ["shutdown"]:
        return {"code": 0, "out": "", "err": "", "restart": True}
    return _dispatch(message, root, identity, state=state)


if __name__ == "__main__":
    if client := sys.argv[3:]:
        os.environ["SMELT_NATIVE_CLIENT"] = client[0]
    _serve_internal(Path(sys.argv[1]), root=sys.argv[2])
