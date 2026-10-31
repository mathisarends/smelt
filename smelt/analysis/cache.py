"""Disposable, content-addressed JSON facts; never deserialize executable objects."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import suppress
from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

from smelt.analysis.identity import analyzer_identity

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_SHA1_LENGTH = 40


class FactCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory / "v1" / analyzer_identity()
        self.hits = 0
        self.misses = 0
        self._connection: sqlite3.Connection | None = None
        self._disabled = False

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

    def missing(self, keys: list[str]) -> set[str]:
        """Plan batched source reads; payloads still go through validated read()."""
        connection = self._connect()
        if connection is None:
            return set(keys)
        present: set[str] = set()
        try:
            for start in range(0, len(keys), 500):
                batch = keys[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                query = f"SELECT key FROM facts WHERE key IN ({placeholders})"  # noqa: S608 - placeholders only; values are bound
                present.update(row[0] for row in connection.execute(query, batch))
        except sqlite3.Error:
            return set(keys)
        return set(keys) - present

    def read_blobs(self, identifiers: list[str]) -> dict[str, bytes]:
        """Validate cached bytes against Git's object ID before using them."""
        connection = self._connect()
        result: dict[str, bytes] = {}
        if connection is None:
            return result
        try:
            for start in range(0, len(identifiers), 500):
                batch = identifiers[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                query = (
                    f"SELECT oid, payload FROM objects WHERE oid IN ({placeholders})"  # noqa: S608 - placeholders only; IDs are bound
                )
                for oid, data in connection.execute(query, batch):
                    if not isinstance(data, bytes):
                        continue
                    digest = hashlib.new(
                        "sha1" if len(oid) == _SHA1_LENGTH else "sha256",
                        usedforsecurity=False,
                    )
                    digest.update(f"blob {len(data)}\0".encode("ascii"))
                    digest.update(data)
                    if digest.hexdigest() == oid:
                        result[oid] = data
        except (ValueError, TypeError, sqlite3.Error):
            return {}
        return result

    def write_blobs(self, contents: dict[str, bytes]) -> None:
        connection = self._connect()
        if connection is not None:
            try:
                connection.executemany(
                    "INSERT OR REPLACE INTO objects VALUES (?, ?)", contents.items()
                )
            except sqlite3.Error:
                self._disable()

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
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS objects (oid TEXT PRIMARY KEY, payload BLOB NOT NULL)"
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
