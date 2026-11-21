"""Disposable, content-addressed JSON facts; never deserialize executable objects."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import suppress
from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

from smelt.analysis.identity import analyzer_identity

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_SHA1_LENGTH = 40
_DAY = 86400
# Entries no run has read or written for this many days are dropped.
_RETENTION_DAYS = 14
# Caches of other analyzer versions no run has opened for this many days.
_STALE_VERSION_DAYS = 7
_INCREMENTAL_VACUUM = 2


class FactCache:
    def __init__(self, directory: Path) -> None:
        self._root = directory
        self._versions = directory / "v1"
        self.directory = self._versions / analyzer_identity(include_rules=False)
        self._hits = 0
        self._misses = 0
        self._connection: sqlite3.Connection | None = None
        self._disabled = False
        self._today = int(time.time() // _DAY)
        self._used_keys: set[str] = set()
        self._used_objects: set[str] = set()

    def close(self) -> None:
        self._disabled = True
        if self._connection is not None:
            try:
                self._mark_used(self._connection)
                self._connection.commit()
                self._prune(self._connection)
                self._connection.close()
                self._connection = None
            except sqlite3.Error:
                self._disable()

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
                        self._used_objects.add(oid)
        except (ValueError, TypeError, sqlite3.Error):
            return {}
        return result

    def write_blobs(self, contents: dict[str, bytes]) -> None:
        connection = self._connect()
        if connection is not None:
            try:
                connection.executemany(
                    "INSERT OR REPLACE INTO objects (oid, payload, used) VALUES (?, ?, ?)",
                    [(oid, data, self._today) for oid, data in contents.items()],
                )
            except sqlite3.Error:
                self._disable()

    def get[T](
        self,
        kind: str,
        inputs: object,
        adapter: TypeAdapter[T],
        *,
        build: Callable[[], T],
    ) -> T:
        key = self.key(kind, inputs)
        result = self.read(key, adapter)
        if result is None:
            result = build()
            self.write(key, result, adapter)
        return result

    def decision_key(self, kind: str, inputs: object) -> str:
        """Rule outputs depend on rule code in addition to the underlying facts."""
        return self.key(kind, [analyzer_identity(), inputs])

    def key(self, kind: str, inputs: object) -> str:
        payload = json.dumps([kind, inputs], sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def read[T](self, key: str, adapter: TypeAdapter[T]) -> T | None:
        connection = self._connect()
        if connection is None:
            self._misses += 1
            return None
        try:
            row = connection.execute(
                "SELECT payload FROM facts WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                self._misses += 1
                return None
            result = adapter.validate_json(row[0])
        except (ValueError, ValidationError, sqlite3.Error):
            self._misses += 1
            return None
        self._hits += 1
        self._used_keys.add(key)
        return result

    def write[T](self, key: str, value: T, adapter: TypeAdapter[T]) -> None:
        connection = self._connect()
        if connection is None:
            return
        try:
            connection.execute(
                "INSERT OR REPLACE INTO facts (key, payload, used) VALUES (?, ?, ?)",
                (key, adapter.dump_json(value), self._today),
            )
        except sqlite3.Error:
            # A read-only checkout, concurrent cleaner or full disk cannot make
            # a disposable optimization change the check's result.
            self._disable()

    def _connect(self) -> sqlite3.Connection | None:
        if self._disabled:
            return None
        if self._connection is None:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                with (
                    suppress(FileExistsError),
                    (self._root / ".gitignore").open("x", encoding="utf-8") as ignore,
                ):
                    ignore.write("*\n")
                self._prune_versions()
                self._connection = sqlite3.connect(
                    self.directory / "facts.sqlite3", timeout=0.02
                )
                # Only takes effect for a new database, before its first table.
                self._connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
                self._connection.execute("PRAGMA journal_mode=WAL")
                self._connection.execute("PRAGMA synchronous=NORMAL")
                for table, column in (("facts", "key"), ("objects", "oid")):
                    self._connection.execute(
                        f"CREATE TABLE IF NOT EXISTS {table} ({column} TEXT PRIMARY KEY, "
                        "payload BLOB NOT NULL, used INTEGER NOT NULL DEFAULT 0)"
                    )
                    _add_used_column(self._connection, table, self._today)
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value INTEGER NOT NULL)"
                )
                self._connection.commit()
            except (OSError, sqlite3.Error):
                self._disable()
        return self._connection

    def _prune_versions(self) -> None:
        """Mark this version's cache as used and drop versions left unused."""
        cutoff = time.time() - _STALE_VERSION_DAYS * _DAY
        with suppress(OSError):
            os.utime(self.directory)
            for sibling in self._versions.iterdir():
                if (
                    sibling != self.directory
                    and sibling.is_dir()
                    and sibling.stat().st_mtime < cutoff
                ):
                    # A version still open elsewhere keeps its locked files.
                    shutil.rmtree(sibling, ignore_errors=True)

    def _mark_used(self, connection: sqlite3.Connection) -> None:
        for table, column, used in (
            ("facts", "key", sorted(self._used_keys)),
            ("objects", "oid", sorted(self._used_objects)),
        ):
            for start in range(0, len(used), 500):
                batch = used[start : start + 500]
                placeholders = ",".join("?" for _ in batch)
                connection.execute(
                    f"UPDATE {table} SET used = ? WHERE used < ? AND {column} IN ({placeholders})",  # noqa: S608 - constant names; values are bound
                    [self._today, self._today, *batch],
                )

    def _prune(self, connection: sqlite3.Connection) -> None:
        """Once a day, drop entries no run has used within the retention window."""
        row = connection.execute(
            "SELECT value FROM meta WHERE name = 'pruned'"
        ).fetchone()
        if row is not None and row[0] >= self._today:
            return
        cutoff = self._today - _RETENTION_DAYS
        connection.execute("DELETE FROM facts WHERE used < ?", (cutoff,))
        connection.execute("DELETE FROM objects WHERE used < ?", (cutoff,))
        connection.execute(
            "INSERT OR REPLACE INTO meta VALUES ('pruned', ?)", (self._today,)
        )
        connection.commit()
        if (
            connection.execute("PRAGMA auto_vacuum").fetchone()[0]
            == _INCREMENTAL_VACUUM
        ):
            connection.execute("PRAGMA incremental_vacuum")

    def _disable(self) -> None:
        self._disabled = True
        if self._connection is not None:
            with suppress(sqlite3.Error):
                self._connection.close()
            self._connection = None


def cache_directory(root: Path) -> Path:
    """Where the fact cache of the project at ``root`` lives.

    A linked Git worktree uses the cache of the same project directory in the
    repository's main checkout. Facts are content-addressed, so a fresh worktree
    starts from the facts its siblings already computed. Every other project,
    and a worktree whose project directory does not exist in the main checkout,
    keeps ``<root>/.smelt_cache``.
    """
    local = root / ".smelt_cache"
    linked = _linked_worktree(root)
    if linked is None:
        return local
    top, main = linked
    shared = main / root.relative_to(top)
    return shared / ".smelt_cache" if shared.is_dir() else local


def _add_used_column(connection: sqlite3.Connection, table: str, today: int) -> None:
    """Upgrade an older cache in place; its entries count as used today."""
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if "used" not in columns:
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN used INTEGER NOT NULL DEFAULT {today:d}"
        )


def _linked_worktree(root: Path) -> tuple[Path, Path] | None:
    """The top of the linked worktree containing ``root`` and its main checkout."""
    for top in (root, *root.parents):
        marker = top / ".git"
        if marker.is_dir():
            return None
        if not marker.is_file():
            continue
        try:
            text = marker.read_text(encoding="utf-8").strip()
            if not text.startswith("gitdir:"):
                return None
            git_dir = top / text.removeprefix("gitdir:").strip()
            common = (
                git_dir / (git_dir / "commondir").read_text(encoding="utf-8").strip()
            ).resolve()
        except OSError:
            # Submodules have no commondir; unreadable metadata keeps the local cache.
            return None
        # A bare repository has no main checkout to share.
        if common.name != ".git":
            return None
        return top, common.parent
    return None
