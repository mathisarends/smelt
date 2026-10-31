"""Explicit, scoped ordering fixes; analysis and normal checks remain read-only."""

from __future__ import annotations

import codecs
import io
import os
import stat
import tempfile
import tokenize
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from smelt.analysis.ordering_rewrite import rewrite_order
from smelt.analysis.parsing import AnalysisError

if TYPE_CHECKING:
    from smelt.engine.check import CheckOutcome


@dataclass
class OrderingFixes:
    files: list[str] = field(default_factory=list)
    sections: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)

    def skip(self, path: str, reason: str) -> None:
        self.skipped.append({"path": path, "reason": reason})

    def to_json(self) -> dict[str, Any]:
        return {"files": self.files, "sections": self.sections, "skipped": self.skipped}


@dataclass(frozen=True)
class _Prepared:
    path: str
    target: Path
    original: bytes
    replacement: bytes
    sections: int


def apply_ordering_fixes(outcome: CheckOutcome) -> OrderingFixes:
    """Fix only visible SMT313 sections after scope, selection, ignores and debt."""
    fixes = OrderingFixes()
    selected: dict[str, set[str]] = defaultdict(set)
    for violation in outcome.report.violations:
        if violation.code != "SMT313" or violation.path is None:
            continue
        expected = violation.expected or {}
        if not expected.get("fixable") or not isinstance(expected.get("section"), str):
            fixes.skip(
                violation.path,
                str(expected.get("fix_reason") or "section requires manual review"),
            )
            continue
        selected[violation.path].add(expected["section"])
    pending: list[_Prepared] = []
    for path, keys in sorted(selected.items()):
        try:
            prepared = _prepare(outcome, path, keys)
            if prepared.replacement != prepared.original:
                pending.append(prepared)
        except (AnalysisError, OSError, UnicodeError, SyntaxError) as exc:
            fixes.skip(path, str(exc))
    for prepared in pending:
        try:
            _write(prepared, outcome.context.root)
            fixes.files.append(prepared.path)
            fixes.sections += prepared.sections
        except (AnalysisError, OSError) as exc:
            fixes.skip(prepared.path, str(exc))
    return fixes


def _local_target(target: Path, root: Path) -> bool:
    return target.resolve().is_relative_to(root) and not any(
        part.is_symlink()
        for part in (target, *target.parents)
        if part.is_relative_to(root)
    )


def _prepare(outcome: CheckOutcome, path: str, keys: set[str]) -> _Prepared:
    ctx = outcome.context
    source = ctx.files.source_for_path(path)
    if source is None or not _local_target(source.absolute, ctx.root):
        raise AnalysisError(
            "safe fixes require a regular source file inside the project root"
        )
    result = ctx.ordering.files[path]
    sections = [
        section for section in result.sections if section.key in keys and section.edges
    ]
    original = source.absolute.read_bytes()
    encoding, _ = tokenize.detect_encoding(io.BytesIO(original).readline)
    if codecs.lookup(encoding).name not in {"utf-8", "utf-8-sig"}:
        raise AnalysisError("safe ordering fixes support UTF-8 source only")
    text = original.decode(encoding)
    if text.replace("\r\n", "\n").replace("\r", "\n") != ctx.files.read_text(path):
        raise AnalysisError("source changed since the check; run the check again")
    rewritten = rewrite_order(text, result, sections)
    return _Prepared(
        path, source.absolute, original, rewritten.encode(encoding), len(sections)
    )


def _write(prepared: _Prepared, root: Path) -> None:
    target = prepared.target
    if not _local_target(target, root) or target.read_bytes() != prepared.original:
        raise AnalysisError("source changed since fix preparation; no replacement made")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".smelt-order-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(prepared.replacement)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(stat.S_IMODE(target.stat().st_mode))
        if not _local_target(target, root) or target.read_bytes() != prepared.original:
            raise AnalysisError(
                "source changed during fix preparation; no replacement made"
            )
        temporary.replace(target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.chmod(stat.S_IMODE(temporary.stat().st_mode) | stat.S_IWUSR)
            temporary.unlink()
