"""Explicit, scoped ordering fixes; analysis and normal checks remain read-only."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from smelt.analysis.ordering_rewrite import rewrite_order
from smelt.analysis.parsing import AnalysisError
from smelt.engine.source_fixes import SourceEdit

if TYPE_CHECKING:
    from smelt.engine.check import CheckOutcome


@dataclass(frozen=True)
class OrderingFixes:
    files: tuple[str, ...]
    sections: int
    skipped: tuple[dict[str, str], ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "files": list(self.files),
            "sections": self.sections,
            "skipped": list(self.skipped),
        }


@dataclass(frozen=True)
class _Prepared:
    edit: SourceEdit
    sections: int


def apply_ordering_fixes(outcome: CheckOutcome) -> OrderingFixes:
    """Fix only visible SMT313 sections after scope, selection, ignores and debt."""
    files: list[str] = []
    sections = 0
    skipped: list[dict[str, str]] = []
    selected: dict[str, set[str]] = defaultdict(set)
    for violation in outcome.report.violations:
        if violation.code != "SMT313" or violation.path is None:
            continue
        expected = violation.expected or {}
        if not expected.get("fixable") or not isinstance(expected.get("section"), str):
            reason = expected.get("fix_reason") or "section requires manual review"
            skipped.append({"path": violation.path, "reason": str(reason)})
            continue
        selected[violation.path].add(expected["section"])
    pending: list[_Prepared] = []
    for path, keys in sorted(selected.items()):
        try:
            prepared = _prepare(outcome, path, keys)
            if prepared.edit.replacement != prepared.edit.original:
                pending.append(prepared)
        except (AnalysisError, OSError, UnicodeError, SyntaxError) as exc:
            skipped.append({"path": path, "reason": str(exc)})
    for prepared in pending:
        try:
            prepared.edit.write(outcome.context.root)
            files.append(prepared.edit.path)
            sections += prepared.sections
        except (AnalysisError, OSError) as exc:
            skipped.append({"path": prepared.edit.path, "reason": str(exc)})
    return OrderingFixes(files=tuple(files), sections=sections, skipped=tuple(skipped))


def _prepare(outcome: CheckOutcome, path: str, keys: set[str]) -> _Prepared:
    ctx = outcome.context
    result = ctx.ordering.files[path]
    sections = [
        section for section in result.sections if section.key in keys and section.edges
    ]
    return _Prepared(
        SourceEdit.prepare(
            ctx, path, lambda text: rewrite_order(text, result, sections)
        ),
        len(sections),
    )
