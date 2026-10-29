from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class DefinitionOrder(BaseRule):
    code = "SMT313"
    name = "definition-order"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.ORDERING})
    doc = RuleDoc(
        summary="Definitions can follow the configured order without violating modeled execution dependencies.",
        rationale=(
            "Public interfaces should be easy to find. A stable topological order "
            "prioritizes configured categories while preserving category order, "
            "definition-time reads, rebinding and execution barriers. Function bodies "
            "are deferred; decorators, defaults, assignments and eager annotations "
            "are not. Future imports and declared Python 3.14+ targets defer annotations. "
            "Overload/property families remain atomic. Unknown execution is pinned."
        ),
        bad="def _load():\n    return 1\n\ndef get():\n    return _load()",
        good="def get():\n    return _load()\n\ndef _load():\n    return 1",
        fix=(
            "Review the expected order and execution barriers. Explicit check --fix "
            "can apply bounded moves where the package permits safe-only fixes. "
            "Imports, unknown execution and a misplaced main guard require manual "
            "review. Names and visibility are never rewritten by ordering."
        ),
        config=("structure.contracts", "project.python_version"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for result in ctx.ordering.files.values():
            for issue in result.issues:
                section = issue.section
                reason = issue.reason or (section.fix_reason if section else None)
                yield self.violation(
                    issue.message,
                    path=result.path,
                    line=issue.line,
                    source_module=result.module,
                    expected={
                        "contract": result.contract,
                        "scope": issue.scope,
                        "ordering_edges": list(issue.edges),
                        "section": section.key if section else None,
                        "original": section.original if section else None,
                        "order": section.expected if section else None,
                        "fixable": section is not None and reason is None,
                        "fix_reason": reason,
                        "deferred_annotations": result.deferred,
                    },
                    hint=f"Keep execution boundaries and prerequisite definitions in place. {reason or 'Use the expected order; bounded fixes require explicit --fix.'}",
                )
