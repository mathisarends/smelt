from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from smelt.analysis.cache import FactCache, cache_directory
from smelt.engine.check import CheckOptions
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

TEXT = TypeAdapter(str)
DAY = 86400


@pytest.fixture(autouse=True)
def _python_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The analysis cache serves only the Python engine."""
    monkeypatch.setenv("SMELT_CORE", "python")


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


def test_surplus_analyzer_versions_are_removed_oldest_first(tmp_path: Path) -> None:
    versions = tmp_path / "v1"
    now = time.time()
    for age in range(10):
        directory = versions / f"opened-{age}-hours-ago"
        directory.mkdir(parents=True)
        (directory / "facts.sqlite3").write_bytes(b"")
        os.utime(directory, (now - age * 3600, now - age * 3600))

    _write(tmp_path, "fact")

    assert sorted(path.name for path in versions.iterdir()) == sorted(
        [
            *(f"opened-{age}-hours-ago" for age in range(7)),
            FactCache(tmp_path).directory.name,
        ]
    )


def test_facts_are_stored_compressed(tmp_path: Path) -> None:
    cache = FactCache(tmp_path)
    cache.write("repetitive", "fact " * 2000, TEXT)
    cache.close()

    connection = sqlite3.connect(cache.directory / "facts.sqlite3")
    (stored,) = connection.execute("SELECT length(payload) FROM facts").fetchone()
    connection.close()
    reopened = FactCache(tmp_path)
    restored = reopened.read("repetitive", TEXT)
    reopened.close()

    assert stored < 500
    assert restored == "fact " * 2000


def test_uncompressed_payloads_of_older_caches_are_still_read(tmp_path: Path) -> None:
    database = _write(tmp_path, "plain")
    connection = sqlite3.connect(database)
    connection.execute("UPDATE facts SET payload = ?", (b'"fact"',))
    connection.commit()
    connection.close()

    cache = FactCache(tmp_path)
    restored = cache.read("plain", TEXT)
    cache.close()

    assert restored == "fact"


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


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
        timeout=10,
    )


@pytest.fixture
def main(tmp_path: Path) -> Path:
    """A main checkout with a nested project and one commit."""
    root = tmp_path / "main"
    (root / "service").mkdir(parents=True)
    (root / "service" / "smelt.yml").write_text("version: 2\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _worktree(main: Path, name: str = "linked") -> Path:
    path = main.parent / name
    _git(main, "worktree", "add", "-q", "--detach", str(path))
    return path


@pytest.mark.parametrize("project", [".", "service"])
def test_linked_worktree_shares_the_main_checkout_cache(
    main: Path, project: str
) -> None:
    linked = _worktree(main)

    assert cache_directory(linked / project) == main / project / ".smelt_cache"


def test_main_checkout_keeps_its_own_cache(main: Path) -> None:
    _worktree(main)

    assert cache_directory(main / "service") == main / "service" / ".smelt_cache"


def test_project_missing_from_the_main_checkout_keeps_a_local_cache(
    main: Path,
) -> None:
    linked = _worktree(main)
    (linked / "added").mkdir()

    assert cache_directory(linked / "added") == linked / "added" / ".smelt_cache"


def test_directory_outside_git_keeps_a_local_cache(tmp_path: Path) -> None:
    assert cache_directory(tmp_path) == tmp_path / ".smelt_cache"


def test_worktree_of_a_bare_repository_keeps_a_local_cache(
    main: Path, tmp_path: Path
) -> None:
    bare = tmp_path / "bare.git"
    _git(tmp_path, "clone", "-q", "--bare", str(main), str(bare))
    linked = tmp_path / "from-bare"
    _git(bare, "worktree", "add", "-q", "--detach", str(linked))

    assert cache_directory(linked) == linked / ".smelt_cache"


def test_check_in_a_linked_worktree_writes_facts_to_the_main_checkout(
    main: Path,
) -> None:
    write_project(
        main,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
        },
    )
    _git(main, "add", ".")
    _git(main, "commit", "-q", "-m", "app")
    linked = _worktree(main)

    check(linked)

    assert (main / ".smelt_cache").is_dir()
    assert not (linked / ".smelt_cache").exists()


def test_shared_facts_follow_module_renames_and_removals_in_each_worktree(
    main: Path,
) -> None:
    write_project(
        main,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [app]}\n"
            "rules: {imports/prefer-package-import: warning}\n",
            "app/__init__.py": "",
            "app/api/__init__.py": "from .legacy import Client\n",
            "app/api/legacy.py": "class Client: ...\n",
            "app/caller.py": "from app.api.legacy import Client\n",
        },
    )
    _git(main, "add", ".")
    _git(main, "commit", "-qm", "package API and caller")
    renamed = _worktree(main, "renamed")
    removed = _worktree(main, "removed")
    check(main)

    _git(renamed, "mv", "app/api/legacy.py", "app/api/client.py")
    write_project(
        renamed,
        {
            "app/api/__init__.py": "from .client import Client\n",
            "app/caller.py": "from app.api.client import Client\n",
        },
    )
    _git(removed, "rm", "app/api/legacy.py")
    write_project(removed, {"app/api/__init__.py": ""})

    for _ in range(2):
        renamed_report = check(renamed).report
        removed_report = check(removed).report
        assert renamed_report == check(renamed, CheckOptions(use_cache=False)).report
        assert removed_report == check(removed, CheckOptions(use_cache=False)).report
        [finding] = renamed_report.violations
        assert (finding.code, finding.path, finding.target_module) == (
            "IMP004",
            "app/caller.py",
            "app.api.client",
        )
        assert removed_report.violations == []
