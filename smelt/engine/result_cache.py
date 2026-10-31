"""Exact staged CLI results, checked before loading config and analysis schemas."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from contextlib import closing, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, TextIO

from smelt.analysis.identity import analyzer_identity
from smelt.engine.changes import (
    GitIndex,
    capture_index,
    git_repository,
    lexical_path,
    resolve_comparison,
)

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

_RESULT_FIELDS = 5


class RecordingStream(io.TextIOBase):
    def __init__(self, destination: TextIO) -> None:
        self.destination = destination
        self.parts: list[str] = []

    def write(self, text: str) -> int:
        self.parts.append(text)
        return self.destination.write(text)

    def isatty(self) -> bool:
        return self.destination.isatty()

    def flush(self) -> None:
        with suppress(ValueError):
            self.destination.flush()


@dataclass(frozen=True)
class ResultCache:
    path: Path
    key: str
    family: str
    index: GitIndex
    format: str

    @classmethod
    def prepare(
        cls, args: argparse.Namespace, cwd: Path, *, color: bool
    ) -> ResultCache | None:
        if args.command != "check" or not args.staged or args.no_cache or args.fix:
            return None
        requested = lexical_path(cwd / args.config) if args.config is not None else None
        repository = git_repository(cwd, requested)
        if requested is not None and not requested.is_relative_to(repository):
            return None
        index = capture_index(repository)
        args._staged_index = index
        _, comparison = resolve_comparison(repository, args.base, repository=repository)
        args._comparison = comparison
        config = requested
        if config is None:
            for directory in (cwd.resolve(), *cwd.resolve().parents):
                if not directory.is_relative_to(repository):
                    break
                config = next(
                    (
                        directory / name
                        for name in ("smelt.yaml", "smelt.yml")
                        if (directory / name).relative_to(repository).as_posix()
                        in index.blobs
                    ),
                    None,
                )
                if config is not None:
                    break
        if (
            config is None
            or config.relative_to(repository).as_posix() not in index.blobs
        ):
            return None
        options = {
            key: value
            for key, value in vars(args).items()
            if key != "handler" and not key.startswith("_")
        }
        inputs = [
            2,
            str(cwd.resolve()),
            str(config),
            comparison.to_json(),
            options,
            color,
        ]
        key = hashlib.sha256(
            json.dumps([inputs, index.identity], sort_keys=True).encode()
        ).hexdigest()
        # Inventory protects negative existence queries, package/namespace discovery
        # and workspace globbing. Contents only need to match observed inputs.
        family = hashlib.sha256(
            json.dumps([inputs, sorted(index.blobs)], sort_keys=True).encode()
        ).hexdigest()
        return cls(
            config.parent
            / ".smelt_cache"
            / "v1"
            / analyzer_identity()
            / "results.sqlite3",
            key,
            family,
            index,
            args.format,
        )

    def read(self) -> tuple[int, str, str] | None:
        if not self.path.is_file():
            return None
        try:
            with closing(
                sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True, timeout=0.02)
            ) as connection:
                for key in (self.key, self.family):
                    row = connection.execute(
                        "SELECT payload, digest FROM results WHERE key = ?", (key,)
                    ).fetchone()
                    if row is None or hashlib.sha256(row[0]).hexdigest() != row[1]:
                        continue
                    data = json.loads(row[0])
                    if not self._valid(data):
                        continue
                    code, out, err, previous, dependencies = data
                    if previous != self.index.identity:
                        if not dependencies or any(
                            self.index.blobs.get(path) != oid
                            for path, oid in dependencies.items()
                        ):
                            continue
                        out = self._refresh(out)
                    return code, out, err
        except (OSError, ValueError, TypeError, KeyError, IndexError, sqlite3.Error):
            return None
        return None

    def _valid(self, data: object) -> bool:
        return (
            isinstance(data, list)
            and len(data) == _RESULT_FIELDS
            and type(data[0]) is int
            and data[0] in {0, 1}
            and all(isinstance(data[index], str) for index in (1, 2, 3))
            and isinstance(data[4], dict)
            and all(
                isinstance(path, str) and isinstance(oid, str)
                for path, oid in data[4].items()
            )
        )

    def _refresh(self, output: str) -> str:
        if self.format not in {"json", "sarif"}:
            return output
        report = json.loads(output)
        comparison = (
            report["comparison"]
            if self.format == "json"
            else report["runs"][0]["properties"]["comparison"]
        )
        comparison["index_hash"] = self.index.identity
        return json.dumps(report, indent=2, ensure_ascii=False) + "\n"

    def write(
        self,
        code: int,
        out: str,
        err: str,
        *,
        dependencies: dict[str, str] | None = None,
    ) -> None:
        if code not in {0, 1}:
            return
        payload = json.dumps(
            [code, out, err, self.index.identity, dependencies or {}]
        ).encode()
        rows = [(self.key, payload, hashlib.sha256(payload).hexdigest())]
        if dependencies:
            rows.append((self.family, payload, rows[0][2]))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with (
                closing(sqlite3.connect(self.path, timeout=0.02)) as connection,
                connection,
            ):
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, payload BLOB NOT NULL, digest TEXT NOT NULL)"
                )
                connection.executemany(
                    "INSERT OR REPLACE INTO results VALUES (?, ?, ?)",
                    rows,
                )
        except (OSError, sqlite3.Error):
            return
