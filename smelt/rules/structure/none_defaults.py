from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.none_default_rewrite import rewrite_none_defaults
from smelt.analysis.none_defaults import none_defaults
from smelt.analysis.parsing import AnalysisError
from smelt.diagnostics.fixes import (
    Applicability,
    Fix,
    FixAvailability,
    TextEdit,
    source_offset,
)
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.none_defaults import NoneDefault


class ImplicitOptionalDefault(Rule):
    file_local = True
    code = "TYP003"
    name = "typing/implicit-optional-default"
    category = Category.TYPING
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="An optional parameter with a None default uses truthiness to choose a fallback.",
        rationale="For an explicitly optional parameter, None commonly means not supplied. Using parameter or fallback also replaces valid falsy values such as zero, empty strings, empty containers and objects with custom truthiness. This opt-in review recognizes two-operand or expressions whose first operand is a directly named, unrebound parameter with a literal None default and a syntactic optional annotation (including quoted unions and typing Optional/Union/Annotated). It skips nested scopes, boolean tests, and parameters rebound anywhere in their function. Intentional falsy defaults remain valid; suppress or disable the review there. This is bounded syntax analysis, not type or control-flow inference.",
        bad="def create(options: Options | None = None):\n    self.options = options or Options()",
        good="def create(options: Options | None = None):\n    self.options = options if options is not None else Options()",
        fix="Review whether only None should select the fallback. If so, use parameter if parameter is not None else fallback. smelt check --select TYP003 --fix --unsafe-fixes applies this behavior-changing rewrite only to visible findings. Plain --fix leaves it unchanged. The rewrite preserves fallback laziness and source outside each expression; inline comments or explicit line continuations within an expression require manual editing. Intentional truthiness fallback should remain unchanged. Debt and changed comparisons distinguish enclosing functions, including class and nested-function names. Older debt without a function identity requires review and explicit re-recording; debt --prune does not accept it under a new function.",
        config=("rules", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            syntax = ctx.syntax.for_path(source.path)
            if syntax is None:
                continue
            text = ctx.files.read_text(source.path)
            for candidate in none_defaults(syntax.tree, syntax.bindings):
                node = candidate.node
                yield self.violation(
                    f"optional parameter {candidate.parameter!r} uses truthiness for its default",
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    end_line=node.end_lineno,
                    end_column=(node.end_col_offset or 0) + 1,
                    source_module=source.module,
                    expected={
                        "parameter": candidate.parameter,
                        "scope": candidate.scope,
                        "fix_safety": "unsafe",
                    },
                    hint="Use an explicit is not None conditional if only None should select the fallback; review intentional falsy defaults.",
                    fix=_fix(text, candidate),
                )


def _fix(text: str, candidate: NoneDefault) -> Fix | None:
    """Unsafe: falsy values stop selecting the fallback and __bool__ no longer runs."""
    node = candidate.node
    try:
        rewritten = rewrite_none_defaults(text, [candidate])
    except AnalysisError:
        # Comments or continuations inside the expression need a human edit.
        return None
    span = (
        source_offset(text, node.lineno, node.col_offset),
        source_offset(text, node.end_lineno or node.lineno, node.end_col_offset or 0),
    )
    return Fix(
        Applicability.UNSAFE,
        f"Select the fallback only when {candidate.parameter!r} is None",
        (TextEdit.between(text, rewritten, span=span),),
    )
