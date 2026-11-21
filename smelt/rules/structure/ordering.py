from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.ordering_rewrite import rewrite_order
from smelt.analysis.parsing import AnalysisError
from smelt.diagnostics.fixes import Applicability, Fix, FixAvailability, TextEdit
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.ordering import FileOrder


class PublicDefinitionsFirst(Rule):
    code = "LAY004"
    name = "layout/definition-order"
    category = Category.LAYOUT
    default_severity = Severity.ERROR
    requires = frozenset({Index.ORDERING})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="Definitions are out of public-first order within modeled execution boundaries.",
        rationale=(
            "Public interfaces should be easy to find. A stable topological order "
            "prioritizes configured categories while preserving category order, "
            "definition-time reads, rebinding and execution barriers. Function bodies "
            "are deferred; decorators, defaults, assignments and eager annotations "
            "are not. Future imports and declared Python 3.14+ targets defer annotations. "
            "Unions of recognized, unshadowed builtin types (and None) are modeled "
            "in eager function annotations for declared Python 3.10+ targets; imported "
            "or user-defined types and operator expressions in defaults remain unknown. "
            "Overload/property families remain atomic. Unknown execution is pinned."
            " Attribute dictionaries of unambiguous undecorated nested functions are distinct from the enclosing ordered namespace, so vars(local_function) and local_function.__dict__ alone do not block enclosing moves. Nonlocal closure writes alone are also unrelated to module/class definition order. Unknown receivers, globals, locals, vars(), global writes and code/annotation introspection still require review."
        ),
        bad="def _load():\n    return 1\n\ndef get():\n    return _load()",
        good="def get():\n    return _load()\n\ndef _load():\n    return 1",
        fix=(
            "Review the expected order and execution barriers. Explicit check --fix "
            "can apply bounded moves where the package permits safe-only fixes. "
            "Imports, unknown execution and a misplaced main guard require manual "
            "review. Names and visibility are never rewritten by ordering."
            " Refused sections identify the triggering expression and its source "
            "location in fix_blocker and fix_reason; the safety boundary stays in place."
            " Pinned definitions are listed under coverage.ordering[].barriers with "
            "check --format json --show-coverage. Dependencies take precedence over "
            "public-first order. On Python 3.12/3.13, keep a needed annotations future "
            "import when the chosen order uses forward references; TYP002 retains it."
        ),
        canonical=(
            "Put public definitions before private ones, in the configured "
            "section order"
        ),
        config=("rules", "style.overrides", "project.python_version"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for result in ctx.ordering.files.values():
            fixes = _SectionFixes(ctx, result.path)
            for finding in result.findings:
                expected = dict(finding.expected)
                fix = None
                if expected["fixable"]:
                    try:
                        fix = fixes.for_section(str(expected["section"]))
                    except AnalysisError as exc:
                        expected["fix_reason"] = str(exc)
                    expected["fixable"] = fix is not None
                reason = expected["fix_reason"]
                yield self.violation(
                    finding.message,
                    path=result.path,
                    line=finding.line,
                    source_module=result.module,
                    expected=expected,
                    hint=f"Keep execution boundaries and prerequisite definitions in place. {reason or 'Use the expected order; bounded fixes require explicit --fix.'}",
                    fix=fix,
                )


class _SectionFixes:
    """Rebuild a file's ordering sections only when one of its findings is fixable."""

    def __init__(self, ctx: AnalysisContext, path: str) -> None:
        self._ordering = ctx.ordering
        self._files = ctx.files
        self._path = path
        self._order: FileOrder | None = None

    def for_section(self, key: str) -> Fix | None:
        if self._order is None:
            self._order = self._ordering.for_rewrite(self._path)
        section = next(
            (s for s in self._order.sections if s.key == key and s.edges), None
        )
        if section is None:
            return None
        text = self._files.read_text(self._path)
        rewritten = rewrite_order(text, self._order, [section])
        return Fix(
            Applicability.SAFE,
            "Move the section's definitions into the expected order",
            (TextEdit.between(text, rewritten, whole_lines=True),),
        )
