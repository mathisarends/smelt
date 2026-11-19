from __future__ import annotations

import os
import sqlite3
import time
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from smelt.analysis.cache import FactCache

if TYPE_CHECKING:
    from pathlib import Path

TEXT = TypeAdapter(str)
DAY = 86400


def _write(root: Path, key: str) -> Path:
    cache = FactCache(root)
    cache.write(key, "fact", TEXT)
    cache.close()
    return cache.directory / "facts.sqlite3"


def _age(database: Path, days: int) -> None:
    """Pretend no run used the entries for ``days`` and none pruned today."""
    connection = sqlite3.connect(database)
    connection.execute("UPDATE facts SET used = used - ?", (days,))
    connection.execute("DELETE FROM meta")
    connection.commit()
    connection.close()


def _keys(database: Path) -> list[str]:
    connection = sqlite3.connect(database)
    keys = [row[0] for row in connection.execute("SELECT key FROM facts")]
    connection.close()
    return keys


def test_entries_unused_beyond_the_retention_are_pruned(tmp_path: Path) -> None:
    database = _write(tmp_path, "stale")
    _age(database, 20)

    _write(tmp_path, "fresh")

    assert _keys(database) == ["fresh"]


def test_reading_an_entry_keeps_it(tmp_path: Path) -> None:
    database = _write(tmp_path, "read")
    _age(database, 20)

    cache = FactCache(tmp_path)
    assert cache.read("read", TEXT) == "fact"
    cache.close()

    assert _keys(database) == ["read"]


def test_entries_within_the_retention_survive(tmp_path: Path) -> None:
    database = _write(tmp_path, "recent")
    _age(database, 10)

    _write(tmp_path, "fresh")

    assert sorted(_keys(database)) == ["fresh", "recent"]


def test_caches_of_unused_analyzer_versions_are_removed(tmp_path: Path) -> None:
    versions = tmp_path / "v1"
    stale, recent = versions / "stale", versions / "recent"
    for directory in (stale, recent):
        directory.mkdir(parents=True)
        (directory / "facts.sqlite3").write_bytes(b"")
    old = time.time() - 8 * DAY
    os.utime(stale, (old, old))

    _write(tmp_path, "fact")

    assert sorted(path.name for path in versions.iterdir()) == sorted(
        ["recent", FactCache(tmp_path).directory.name]
    )


def test_older_caches_are_upgraded_and_keep_their_entries(tmp_path: Path) -> None:
    cache = FactCache(tmp_path)
    cache.directory.mkdir(parents=True)
    connection = sqlite3.connect(cache.directory / "facts.sqlite3")
    connection.execute(
        "CREATE TABLE facts (key TEXT PRIMARY KEY, payload BLOB NOT NULL)"
    )
    connection.execute("INSERT INTO facts VALUES ('old', ?)", (b'"fact"',))
    connection.commit()
    connection.close()

    assert cache.read("old", TEXT) == "fact"
    cache.close()

    assert _keys(cache.directory / "facts.sqlite3") == ["old"]
