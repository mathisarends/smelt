"""An immutable, in-memory project view addressed by captured Git object IDs."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import TYPE_CHECKING

from smelt.analysis.errors import AnalysisError
from smelt.config.source import SourceStore
from smelt.engine.changes import _git, read_blobs

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from smelt.analysis.cache import FactCache
    from smelt.engine.changes import GitIndex


class GitSourceStore(SourceStore):
    def __init__(
        self, repository: Path, blobs: dict[str, str], *, cache: FactCache | None = None
    ) -> None:
        self.repository = repository
        self.blobs = blobs
        self.cache = cache
        self._contents: dict[str, bytes] = {}
        self._dependencies: dict[str, str] = {}
        self._children: dict[Path, set[Path]] = {repository: set()}
        self._objects = {repository / path: oid for path, oid in blobs.items()}
        self._files = set(self._objects)
        for file_path in self._files:
            path = file_path
            while path != repository:
                self._children.setdefault(path.parent, set()).add(path)
                path = path.parent

    @classmethod
    def from_index(cls, index: GitIndex) -> GitSourceStore:
        return cls(index.repository, index.blobs)

    @classmethod
    def from_revision(
        cls, repository: Path, revision: str, *, cache: FactCache | None = None
    ) -> GitSourceStore:
        blobs: dict[str, str] = {}
        for record in _git(repository, "ls-tree", "-rz", "--full-tree", revision).split(
            b"\0"
        ):
            if not record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, oid = metadata.split()
            path = Path(os.fsdecode(raw_path))
            if kind != b"blob":
                continue
            if mode == b"120000":
                raise AnalysisError(
                    f"changed/staged snapshots do not support symbolic links: {path}"
                )
            if path.drive or path.is_absolute() or ".." in path.parts:
                raise AnalysisError(f"unsafe path in Git snapshot: {path}")
            blobs[path.as_posix()] = oid.decode("ascii")
        return cls(repository, blobs, cache=cache)

    def resolve(self, path: Path) -> Path:
        # Working-tree symlinks and missing directories cannot change a Git view.
        return Path(os.path.abspath(path))  # noqa: PTH100 - Git paths must not follow working-tree symlinks

    def is_file(self, path: Path) -> bool:
        return path in self._files or self.resolve(path) in self._files

    def inventory(self) -> Iterable[Path]:
        return self._files

    def is_dir(self, path: Path) -> bool:
        return path in self._children or self.resolve(path) in self._children

    def exists(self, path: Path) -> bool:
        return self.is_file(path) or self.is_dir(path)

    def iterdir(self, path: Path) -> Iterator[Path]:
        yield from sorted(self._children.get(self.resolve(path), ()))

    def glob(self, path: Path, pattern: str) -> Iterator[Path]:
        root = self.resolve(path)
        parts = Path(pattern).parts

        def visit(directory: Path, remaining: tuple[str, ...]) -> Iterator[Path]:
            if not remaining:
                yield directory
            elif remaining[0] == "**":
                yield from visit(directory, remaining[1:])
                for child in self.iterdir(directory):
                    if self.is_dir(child):
                        yield from visit(child, remaining)
            else:
                for child in self.iterdir(directory):
                    if fnmatch.fnmatchcase(child.name, remaining[0]):
                        yield from visit(child, remaining[1:])

        yield from visit(root, parts)

    def identity(self, path: Path) -> str | None:
        resolved = path if path in self._objects else self.resolve(path)
        oid = self._objects.get(resolved)
        if oid is not None:
            self._dependencies[resolved.relative_to(self.repository).as_posix()] = oid
        return f"git:{oid}" if oid is not None else None

    def dependencies(self) -> dict[str, str]:
        return self._dependencies

    def prefetch(self, paths: Iterable[Path]) -> None:
        identifiers = set()
        for original in paths:
            oid = self._objects.get(original) or self._objects.get(
                self.resolve(original)
            )
            if oid is not None:
                identifiers.add(oid)
        missing = identifiers - self._contents.keys()
        if missing and self.cache is not None:
            self._contents.update(self.cache.read_blobs(sorted(missing)))
            missing -= self._contents.keys()
        if missing:
            fetched = read_blobs(self.repository, sorted(missing))
            self._contents.update(fetched)
            if self.cache is not None:
                self.cache.write_blobs(fetched)

    def read_bytes(self, path: Path) -> bytes:
        identity = self.identity(path)
        if identity is None:
            raise FileNotFoundError(str(path))
        oid = identity.removeprefix("git:")
        if oid not in self._contents:
            self.prefetch([path])
        return self._contents[oid]
