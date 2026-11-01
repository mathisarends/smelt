"""Snapshot-checked, atomic writes shared by explicitly requested source fixes."""

from __future__ import annotations

import codecs
import io
import os
import stat
import tempfile
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from smelt.analysis.parsing import AnalysisError

if TYPE_CHECKING:
    from collections.abc import Callable

    from smelt.analysis.context import AnalysisContext


@dataclass(frozen=True)
class SourceEdit:
    path: str
    target: Path
    original: bytes
    replacement: bytes

    @classmethod
    def prepare(
        cls, ctx: AnalysisContext, path: str, rewrite: Callable[[str], str]
    ) -> SourceEdit:
        source = ctx.files.source_for_path(path)
        if source is None or not _local_target(source.absolute, ctx.root):
            raise AnalysisError(
                "safe fixes require a regular source file inside the project root"
            )
        original = source.absolute.read_bytes()
        encoding, _ = tokenize.detect_encoding(io.BytesIO(original).readline)
        if codecs.lookup(encoding).name not in {"utf-8", "utf-8-sig"}:
            raise AnalysisError("source fixes support UTF-8 source only")
        text = original.decode(encoding)
        if text.replace("\r\n", "\n").replace("\r", "\n") != ctx.files.read_text(path):
            raise AnalysisError("source changed since the check; run the check again")
        return cls(path, source.absolute, original, rewrite(text).encode(encoding))

    def write(self, root: Path) -> None:
        target = self.target
        if not _local_target(target, root) or target.read_bytes() != self.original:
            raise AnalysisError(
                "source changed since fix preparation; no replacement made"
            )
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target.parent, prefix=".smelt-fix-", suffix=".tmp", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(self.replacement)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(stat.S_IMODE(target.stat().st_mode))
            if not _local_target(target, root) or target.read_bytes() != self.original:
                raise AnalysisError(
                    "source changed during fix preparation; no replacement made"
                )
            temporary.replace(target)
        finally:
            if temporary is not None and temporary.exists():
                temporary.chmod(stat.S_IMODE(temporary.stat().st_mode) | stat.S_IWUSR)
                temporary.unlink()


def _local_target(target: Path, root: Path) -> bool:
    return target.resolve().is_relative_to(root) and not any(
        part.is_symlink()
        for part in (target, *target.parents)
        if part.is_relative_to(root)
    )
