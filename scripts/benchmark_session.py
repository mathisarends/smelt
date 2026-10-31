"""Measure a retained Python worker including a fresh client and loopback IPC.

This is a bounded benchmark prototype, not an installed daemon or hook entry.
The worker serializes requests, captures Git afresh and retains imported modules.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

_LIMIT = 1024 * 1024


def request(endpoint: Path, arguments: list[str]) -> dict[str, Any]:
    connection = json.loads(endpoint.read_text(encoding="utf-8"))
    payload = {
        "token": connection["token"],
        "cwd": str(Path.cwd().resolve()),
        "arguments": arguments,
    }
    with socket.create_connection(
        ("127.0.0.1", connection["port"]), timeout=120
    ) as stream:
        stream.sendall(json.dumps(payload).encode() + b"\n")
        with stream.makefile("rb") as incoming:
            response = json.loads(incoming.readline(_LIMIT))
    if response["pid"] != connection["pid"]:
        raise RuntimeError("unexpected worker process")
    if not isinstance(response, dict):
        raise ValueError("invalid worker response")
    return response


def _dispatch(message: dict[str, Any], root: Path, identity: str) -> dict[str, Any]:
    from smelt.analysis.identity import analyzer_identity  # noqa: PLC0415
    from smelt.cli import main  # noqa: PLC0415

    analyzer_identity.cache_clear()
    if analyzer_identity() != identity:
        raise ValueError("analyzer changed; restart this experimental worker")
    arguments = message["arguments"]
    if (
        message["cwd"] != str(root)
        or not isinstance(arguments, list)
        or not arguments
        or arguments[0] != "check"
        or any(not isinstance(value, str) for value in arguments)
    ):
        raise ValueError("worker only accepts checks for its original project")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(arguments)
    return {
        "code": code,
        "out": out.getvalue(),
        "err": err.getvalue(),
        "pid": os.getpid(),
    }


def serve(endpoint: Path) -> None:
    from smelt.analysis.identity import analyzer_identity  # noqa: PLC0415
    from smelt.cli import main  # noqa: F401, PLC0415

    identity = analyzer_identity()
    root = Path.cwd().resolve()
    token = secrets.token_hex(32)
    with socket.create_server(("127.0.0.1", 0)) as listener:
        listener.settimeout(120)
        metadata = {
            "port": listener.getsockname()[1],
            "token": token,
            "pid": os.getpid(),
        }
        pending = endpoint.with_suffix(".pending")
        pending.write_text(json.dumps(metadata), encoding="utf-8")
        pending.replace(endpoint)
        while True:
            stream, _ = listener.accept()
            with stream:
                stream.settimeout(120)
                with stream.makefile("rb") as incoming:
                    message = json.loads(incoming.readline(_LIMIT))
                stopping = False
                try:
                    if message.get("token") != token:
                        raise ValueError("invalid worker token")
                    stopping = message.get("arguments") == ["shutdown"]
                    response = (
                        {"code": 0, "out": "", "err": "", "pid": os.getpid()}
                        if stopping
                        else _dispatch(message, root, identity)
                    )
                except (ValueError, KeyError, TypeError) as exc:
                    response = {
                        "code": 2,
                        "out": "",
                        "err": str(exc),
                        "pid": os.getpid(),
                    }
                stream.sendall(json.dumps(response).encode() + b"\n")
            if stopping:
                break


@contextlib.contextmanager
def session(root: Path, analyzer: Path) -> Iterator[tuple[Path, float]]:
    """Own the worker lifetime, including bounded shutdown after failed checks."""
    with tempfile.TemporaryDirectory(prefix="smelt-session-") as temporary:
        endpoint = Path(temporary) / "endpoint.json"
        started = time.perf_counter()
        with subprocess.Popen(  # noqa: S603 - fixed benchmark worker command
            [sys.executable, str(Path(__file__).resolve()), "serve", str(endpoint)],
            cwd=root,
            env={**os.environ, "PYTHONPATH": str(analyzer)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as process:
            try:
                deadline = time.monotonic() + 30
                while not endpoint.is_file():
                    if process.poll() is not None:
                        _, errors = process.communicate()
                        raise RuntimeError(errors.decode("utf-8", errors="replace"))
                    if time.monotonic() > deadline:
                        raise TimeoutError("worker startup exceeded 30 seconds")
                    time.sleep(0.01)
                yield endpoint, time.perf_counter() - started
            finally:
                if process.poll() is None:
                    with contextlib.suppress(OSError, ValueError):
                        request(endpoint, ["shutdown"])
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


def invoke_client(root: Path, endpoint: Path) -> tuple[float, dict[str, Any]]:
    started = time.perf_counter()
    completed = subprocess.run(  # noqa: S603 - fixed benchmark client command
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "client",
            str(endpoint),
            "check",
            "--staged",
            "--format",
            "json",
            "--fail-on",
            "warning",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode not in {0, 1} or completed.stderr:
        raise RuntimeError(f"worker check failed: {completed.stderr}")
    return elapsed, json.loads(completed.stdout)


def normalized(report: dict[str, Any], root: Path) -> str:
    return json.dumps(report, sort_keys=True).replace(root.as_posix(), "<project>")


def benchmark(args: argparse.Namespace) -> dict[str, Any]:
    from benchmark_checks import (  # noqa: PLC0415
        create_project,
        git,
        invoke,
        summarize,
    )

    with tempfile.TemporaryDirectory(prefix="smelt-worker-benchmark-") as temporary:
        parent = Path(temporary)
        direct, worker = parent / "direct", parent / "worker"
        direct.mkdir()
        changed = create_project(args, direct)
        git(parent, "clone", "--local", "--no-hardlinks", str(direct), str(worker))
        worker_changed = worker / changed.relative_to(direct)
        worker_changed.write_bytes(changed.read_bytes())
        git(worker, "add", worker_changed.relative_to(worker).as_posix())
        samples: dict[str, list[float]] = {
            key: []
            for key in (
                "direct_retry",
                "worker_retry",
                "direct_edit",
                "worker_edit",
            )
        }
        _, first = invoke(direct, args.candidate, "--staged")
        with session(worker, args.candidate) as (endpoint, startup):
            first_worker, result = invoke_client(worker, endpoint)
            if normalized(first, direct) != normalized(result, worker):
                raise RuntimeError("first worker report differs")
            for index in range(args.repeats):
                order = ("direct", "worker") if index % 2 == 0 else ("worker", "direct")
                for mode in order:
                    elapsed, _ = (
                        invoke(direct, args.candidate, "--staged")
                        if mode == "direct"
                        else invoke_client(worker, endpoint)
                    )
                    samples[f"{mode}_retry"].append(elapsed)
            previous = "new_public_helper"
            for index in range(args.repeats):
                name = f"edited_public_helper_{index}"
                for project, source in ((direct, changed), (worker, worker_changed)):
                    source.write_text(source.read_text().replace(previous, name))
                    git(project, "add", source.relative_to(project).as_posix())
                previous = name
                reports: dict[str, dict[str, Any]] = {}
                for mode in (
                    ("direct", "worker") if index % 2 == 0 else ("worker", "direct")
                ):
                    elapsed, result = (
                        invoke(direct, args.candidate, "--staged")
                        if mode == "direct"
                        else invoke_client(worker, endpoint)
                    )
                    samples[f"{mode}_edit"].append(elapsed)
                    reports[mode] = result
                _, independent = invoke(
                    worker, args.candidate, "--staged", "--no-cache"
                )
                if reports["worker"] != independent or normalized(
                    reports["direct"], direct
                ) != normalized(independent, worker):
                    raise RuntimeError("worker edit differs from independent checking")
        return {
            "policy": args.policy,
            "modules": args.modules,
            "methods_per_module": args.methods,
            "repeats": args.repeats,
            "worker_startup_seconds": startup,
            "worker_first_client_check_seconds": first_worker,
            "samples_seconds": samples,
            "distributions_seconds": {
                key: summarize(values) for key, values in samples.items()
            },
            "reports_match_uncached": True,
            "method": "Fresh Python client, loopback IPC, fresh Git capture, analysis and rendering included. Separate cloned repositories share HEAD and staged content, but have independent caches. Only absolute project prefixes are normalized. Alternating execution order. Worker startup/first request reported separately. No retained project indexes or silent fallback.",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    server = commands.add_parser("serve")
    server.add_argument("endpoint", type=Path)
    client = commands.add_parser("client")
    client.add_argument("endpoint", type=Path)
    client.add_argument("arguments", nargs=argparse.REMAINDER)
    run = commands.add_parser("benchmark")
    run.add_argument(
        "--candidate", type=Path, default=Path(__file__).resolve().parent.parent
    )
    run.add_argument(
        "--policy", choices=["members", "architecture", "transitive"], default="members"
    )
    run.add_argument("--modules", type=int, default=250)
    run.add_argument("--methods", type=int, default=12)
    run.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.mode == "serve":
        serve(args.endpoint)
    elif args.mode == "client":
        result = request(args.endpoint, args.arguments)
        sys.stdout.write(result["out"])
        sys.stderr.write(result["err"])
        return int(result["code"])
    else:
        if min(args.modules, args.methods, args.repeats) < 1:
            parser.error("modules, methods and repeats must be positive")
        args.candidate = args.candidate.resolve()
        sys.stdout.write(json.dumps(benchmark(args), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
