"""Disposable, content-addressed JSON facts; never deserialize executable objects."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from contextlib import suppress
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable


@cache
def analyzer_identity() -> str:
    """Invalidate editable checkouts too, even without a package version bump."""
    digest = hashlib.sha256()
    package = Path(__file__).resolve().parent.parent
    for path in sorted(package.rglob("*.py")):
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(path.read_bytes())
    digest.update(str(sys.version_info).encode())
    for dependency in ("smelt-cli", "grimp", "pydantic", "packaging", "pyyaml"):
        try:
            installed = version(dependency)
        except PackageNotFoundError:
            installed = "uninstalled"
        digest.update(f"{dependency}:{installed}".encode())
    return digest.hexdigest()


class FactCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory / "v1" / analyzer_identity()
        self.hits = 0
        self.misses = 0
        self._connection: sqlite3.Connection | None = None
        self._disabled = False

    def _connect(self) -> sqlite3.Connection | None:
        if self._disabled:
            return None
        if self._connection is None:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                self._connection = sqlite3.connect(
                    self.directory / "facts.sqlite3", timeout=0.02
                )
                self._connection.execute("PRAGMA journal_mode=WAL")
                self._connection.execute("PRAGMA synchronous=NORMAL")
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS facts (key TEXT PRIMARY KEY, payload BLOB NOT NULL)"
                )
                self._connection.commit()
            except (OSError, sqlite3.Error):
                self._disable()
        return self._connection

    def _disable(self) -> None:
        self._disabled = True
        if self._connection is not None:
            with suppress(sqlite3.Error):
                self._connection.close()
            self._connection = None

    def close(self) -> None:
        self._disabled = True
        if self._connection is not None:
            try:
                self._connection.commit()
                self._connection.close()
                self._connection = None
            except sqlite3.Error:
                self._disable()

    def key(self, kind: str, inputs: object) -> str:
        payload = json.dumps([kind, inputs], sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def read[T](self, key: str, adapter: TypeAdapter[T]) -> T | None:
        connection = self._connect()
        if connection is None:
            self.misses += 1
            return None
        try:
            row = connection.execute(
                "SELECT payload FROM facts WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                self.misses += 1
                return None
            result = adapter.validate_json(row[0])
        except (ValueError, ValidationError, sqlite3.Error):
            self.misses += 1
            return None
        self.hits += 1
        return result

    def write[T](self, key: str, value: T, adapter: TypeAdapter[T]) -> None:
        connection = self._connect()
        if connection is None:
            return
        try:
            connection.execute(
                "INSERT OR REPLACE INTO facts (key, payload) VALUES (?, ?)",
                (key, adapter.dump_json(value)),
            )
        except sqlite3.Error:
            # A read-only checkout, concurrent cleaner or full disk cannot make
            # a disposable optimization change the check's result.
            self._disable()

    def get[T](
        self, kind: str, inputs: object, adapter: TypeAdapter[T], build: Callable[[], T]
    ) -> T:
        key = self.key(kind, inputs)
        result = self.read(key, adapter)
        if result is None:
            result = build()
            self.write(key, result, adapter)
        return result
