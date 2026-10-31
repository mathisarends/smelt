"""Read-only project storage shared by config discovery and analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path


class SourceStore:
    """Disk implementation; immutable Git views implement the same operations."""

    def resolve(self, path: Path) -> Path:
        return path.resolve()

    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def exists(self, path: Path) -> bool:
        return path.exists()

    def iterdir(self, path: Path) -> Iterator[Path]:
        yield from path.iterdir()

    def glob(self, path: Path, pattern: str) -> Iterator[Path]:
        yield from path.glob(pattern)

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        # Match Path.read_text's universal-newline behavior for Git blobs too.
        return (
            self.read_bytes(path)
            .decode(encoding)
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )

    def identity(self, path: Path) -> str | None:
        return None

    def prefetch(self, paths: Iterable[Path]) -> None:
        """Load several immutable objects in one request, when supported."""

    def inventory(self) -> Iterable[Path] | None:
        return None

    def dependencies(self) -> dict[str, str]:
        """Immutable inputs observed by this view; disk checks cannot reuse these."""
        return {}
