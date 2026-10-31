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
    capture_index,
    git_repository,
    lexical_path,
    resolve_comparison,
)

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

_RESULT_FIELDS = 3


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
        key = hashlib.sha256(
            json.dumps(
                [
                    1,
                    str(cwd.resolve()),
                    str(config),
                    index.identity,
                    comparison.to_json(),
                    options,
                    color,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return cls(
            config.parent
            / ".smelt_cache"
            / "v1"
            / analyzer_identity()
            / "results.sqlite3",
            key,
        )

    def read(self) -> tuple[int, str, str] | None:
        if not self.path.is_file():
            return None
        try:
            with closing(
                sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True, timeout=0.02)
            ) as connection:
                row = connection.execute(
                    "SELECT payload, digest FROM results WHERE key = ?", (self.key,)
                ).fetchone()
            if row is None or hashlib.sha256(row[0]).hexdigest() != row[1]:
                return None
            data = json.loads(row[0])
            if (
                not isinstance(data, list)
                or len(data) != _RESULT_FIELDS
                or type(data[0]) is not int
                or data[0] not in {0, 1}
                or not isinstance(data[1], str)
                or not isinstance(data[2], str)
            ):
                return None
            return data[0], data[1], data[2]
        except (OSError, ValueError, TypeError, sqlite3.Error):
            return None

    def write(self, code: int, out: str, err: str) -> None:
        if code not in {0, 1}:
            return
        payload = json.dumps([code, out, err]).encode()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with (
                closing(sqlite3.connect(self.path, timeout=0.02)) as connection,
                connection,
            ):
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, payload BLOB NOT NULL, digest TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT OR REPLACE INTO results VALUES (?, ?, ?)",
                    (self.key, payload, hashlib.sha256(payload).hexdigest()),
                )
        except (OSError, sqlite3.Error):
            return
