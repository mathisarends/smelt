"""Optional read-only native Git service, owned by the current Python process."""

from __future__ import annotations

import atexit
import base64
import contextlib
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

_LIMIT = 64 * 1024 * 1024
_ENABLED = ContextVar("native_git_enabled", default=False)
_CAPTURED: ContextVar[tuple[Path, str | None, bytes] | None] = ContextVar(
    "native_git_index", default=None
)


class GitService:
    def __init__(self, executable: str) -> None:
        self.executable = executable
        self.process = subprocess.Popen(  # noqa: S603 - supplied by the installed native launcher
            [executable, "--internal-git"],
            cwd=Path(executable).parent,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="smelt-git"
        )

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        return self.executor.submit(self._exchange, message).result(timeout=10)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        self.executor.shutdown(wait=True, cancel_futures=True)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                # Closing a buffered input after the child crashed can itself
                # raise BrokenPipeError. Still close the output pipe as well.
                with contextlib.suppress(OSError):
                    stream.close()

    def _exchange(self, message: dict[str, Any]) -> dict[str, Any]:
        if self.process.stdin is None or self.process.stdout is None:
            raise OSError("closed native Git service")
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline(_LIMIT)
        if not line.endswith(b"\n"):
            raise OSError("incomplete native Git response")
        response = json.loads(line)
        if not isinstance(response, dict):
            raise ValueError("invalid native Git response")
        return response


def close() -> None:
    global _service  # noqa: PLW0603 - one owned service per Python process
    if _service is not None:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            _service.close()
        _service = None


_service: GitService | None = None
_failed = False


atexit.register(close)


@contextlib.contextmanager
def check_scope(*, enabled: bool) -> Iterator[None]:
    active = _ENABLED.set(enabled)
    captured = _CAPTURED.set(None)
    try:
        yield
    finally:
        _CAPTURED.reset(captured)
        _ENABLED.reset(active)


def request(operation: str, root: Path, **values: object) -> dict[str, Any] | None:
    global _service, _failed  # noqa: PLW0603 - serialized helper ownership
    executable = _executable()
    if not executable:
        return None
    try:
        if _service is None:
            _service = GitService(executable)
        response = _service.request(
            {
                "operation": operation,
                "root": str(root),
                "index_file": os.environ.get("GIT_INDEX_FILE"),
                **values,
            }
        )
        return None if "fallback" in response else response
    except (OSError, ValueError, TimeoutError, subprocess.SubprocessError):
        _failed = True
        close()
        return None


def capture(root: Path) -> tuple[Path, str | None] | None:
    response = request("capture", root)
    if response is None:
        return None
    try:
        repository = Path(response["repository"]).resolve()
        head = response["head"]
        if head is not None and not isinstance(head, str):
            return None
        records = base64.b64decode(response["records"], validate=True)
        _CAPTURED.set((repository, os.environ.get("GIT_INDEX_FILE"), records))
        return repository, head
    except (KeyError, TypeError, ValueError):
        return None


def index_records(repository: Path) -> bytes | None:
    if not _executable():
        return None
    captured = _CAPTURED.get()
    if captured is not None and captured[:2] == (
        repository,
        os.environ.get("GIT_INDEX_FILE"),
    ):
        return captured[2]
    response = request("index", repository)
    try:
        return (
            base64.b64decode(response["records"], validate=True) if response else None
        )
    except (KeyError, TypeError, ValueError):
        return None


def read_blobs(repository: Path, identifiers: list[str]) -> dict[str, bytes] | None:
    response = request("blobs", repository, identifiers=identifiers)
    if response is None:
        return None
    try:
        contents = response["blobs"]
        if set(contents) != set(identifiers):
            return None
        return {
            oid: base64.b64decode(contents[oid], validate=True) for oid in identifiers
        }
    except (KeyError, TypeError, ValueError):
        return None


def tree_records(repository: Path, revision: str) -> bytes | None:
    response = request("tree", repository, identifiers=[revision])
    try:
        return (
            base64.b64decode(response["records"], validate=True) if response else None
        )
    except (KeyError, TypeError, ValueError):
        return None


def _executable() -> str | None:
    if (
        not _ENABLED.get()
        or _failed
        or os.environ.get("SMELT_NATIVE_GIT") == "0"
        or any(
            name.startswith("GIT_")
            and name not in {"GIT_PAGER", "GIT_OPTIONAL_LOCKS", "GIT_INDEX_FILE"}
            for name in os.environ
        )
    ):
        return None
    return os.environ.get("SMELT_NATIVE_CLIENT")
