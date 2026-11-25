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
import time
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from smelt.analysis.identity import analyzer_identity
from smelt.cli.app import main
from smelt.cli.session_slots import worker_slot

_LIMIT = 1024 * 1024
_PROTOCOL = 3
_REQUEST_SECONDS = 5
_MAX_ANALYSIS_SECONDS = 300
_MIN_CPU_PROGRESS = 0.05


class Capture(io.StringIO):
    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _serve_internal(
    endpoint: Path, *, root: str | None = None, idle_seconds: float = 120
) -> None:
    with worker_slot(endpoint.parent) as available:
        if available:
            _serve(endpoint, root=root, idle_seconds=idle_seconds)


def _serve(endpoint: Path, *, root: str | None, idle_seconds: float) -> None:
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
                    stream.settimeout(0.25)
                    stream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    acknowledged = threading.Event()
                    done = threading.Event()
                    watchdog = threading.Thread(
                        target=_watch_request,
                        args=(stream, acknowledged, done),
                        daemon=True,
                    )
                    watchdog.start()
                    try:
                        try:
                            response = _respond(
                                stream,
                                token,
                                dispatch=partial(
                                    _dispatch, root=root, identity=identity
                                ),
                                acknowledged=acknowledged,
                            )
                        except (ValueError, KeyError, TypeError, OSError):
                            response = {
                                "code": 2,
                                "out": "",
                                "err": "",
                                "restart": True,
                            }
                        response["pid"] = os.getpid()
                        frame = json.dumps(response).encode() + b"\n"
                    finally:
                        done.set()
                        watchdog.join()
                    with contextlib.suppress(OSError):
                        stream.sendall(frame)
                    if response.get("restart"):
                        break
        finally:
            with contextlib.suppress(OSError, ValueError):
                if json.loads(endpoint.read_text(encoding="utf-8"))["token"] == token:
                    endpoint.unlink()


def _watch_request(
    stream: socket.socket, acknowledged: threading.Event, done: threading.Event
) -> None:
    """Extend an accepted CPU-bound request while retaining idle and total deadlines."""
    started = last_progress = time.monotonic()
    previous_cpu = time.process_time()
    idle_budget = _REQUEST_SECONDS
    while not done.wait(1):
        now = time.monotonic()
        if now - started >= _MAX_ANALYSIS_SECONDS:
            os._exit(2)
        cpu = time.process_time()
        if acknowledged.is_set() and cpu - previous_cpu >= _MIN_CPU_PROGRESS:
            try:
                stream.sendall(b'{"progress":true}\n')
            except OSError:
                os._exit(2)
            last_progress = now
            previous_cpu = cpu
            # Native Git subprocesses can do the next phase of a cold check.
            # Their CPU time is charged to the child process.
            idle_budget = 20
        elif now - last_progress >= idle_budget:
            os._exit(2)


def _respond(
    stream: socket.socket,
    token: str,
    *,
    dispatch: Callable[[dict[str, Any]], dict[str, Any]],
    acknowledged: threading.Event,
) -> dict[str, Any]:
    with stream.makefile("rb") as incoming:
        line = incoming.readline(_LIMIT)
    message = json.loads(line)
    if not isinstance(message, dict) or message.get("token") != token:
        raise ValueError("invalid worker token")
    if message.get("ack") is True:
        # A queued client can time out before the worker reaches its request.
        # Only this connection is abandoned, the worker can serve the next one.
        try:
            stream.sendall(
                json.dumps({"accepted": True, "pid": os.getpid()}).encode() + b"\n"
            )
            with stream.makefile("rb") as incoming:
                confirmed = incoming.readline(6) == b"start\n"
        except OSError:
            confirmed = False
        if not confirmed:
            return {
                "code": 2,
                "out": "",
                "err": "unconfirmed dispatch",
                "restart": False,
            }
        acknowledged.set()
    stream.settimeout(_REQUEST_SECONDS)
    if message.get("arguments") == ["shutdown"]:
        return {"code": 0, "out": "", "err": "", "restart": True}
    return dispatch(message)


def _dispatch(
    message: dict[str, Any],
    root: str,
    identity: str,
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
        contextlib.chdir(root),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        code = main(arguments)
    return {"code": code, "out": out.getvalue(), "err": err.getvalue()}


if __name__ == "__main__":
    if client := sys.argv[3:]:
        os.environ["SMELT_NATIVE_CLIENT"] = client[0]
    _serve_internal(Path(sys.argv[1]), root=sys.argv[2])
