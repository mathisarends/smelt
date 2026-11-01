"""Explicit unsafe fixes for visible optional-parameter defaults."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from smelt.analysis.none_default_rewrite import rewrite_none_defaults
from smelt.analysis.none_defaults import none_defaults
from smelt.analysis.parsing import AnalysisError
from smelt.engine.source_fixes import SourceEdit

if TYPE_CHECKING:
    from smelt.engine.check import CheckOutcome


@dataclass
class NoneDefaultFixes:
    files: list[str] = field(default_factory=list)
    expressions: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "expressions": self.expressions,
            "skipped": self.skipped,
        }


def apply_none_default_fixes(
    outcome: CheckOutcome, *, unsafe: bool = False
) -> NoneDefaultFixes:
    fixes = NoneDefaultFixes()
    selected: dict[str, set[tuple[int | None, int | None]]] = defaultdict(set)
    for violation in outcome.report.violations:
        if violation.code == "SMT321" and violation.path is not None:
            selected[violation.path].add((violation.line, violation.column))
    for path, keys in sorted(selected.items()):
        if not unsafe:
            fixes.skipped.append(
                {
                    "path": path,
                    "reason": "behavior-changing fix requires --unsafe-fixes",
                }
            )
            continue
        try:
            syntax = outcome.context.syntax.for_path(path)
            if syntax is None:
                raise AnalysisError("source syntax is unavailable")
            candidates = [
                candidate
                for candidate in none_defaults(syntax.tree, syntax.bindings)
                if (candidate.node.lineno, candidate.node.col_offset + 1) in keys
            ]
            edit = SourceEdit.prepare(
                outcome.context,
                path,
                partial(rewrite_none_defaults, candidates=candidates),
            )
            if edit.replacement != edit.original:
                edit.write(outcome.context.root)
                fixes.files.append(path)
                fixes.expressions += len(candidates)
        except (AnalysisError, OSError, UnicodeError, SyntaxError) as exc:
            fixes.skipped.append({"path": path, "reason": str(exc)})
    return fixes
