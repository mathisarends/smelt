from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.none_defaults import none_defaults
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class ImplicitOptionalDefault(BaseRule):
    file_local = True
    code = "SMT321"
    name = "implicit-optional-default"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="An optional parameter with a None default uses truthiness to choose a fallback.",
        rationale="For an explicitly optional parameter, None commonly means not supplied. Using parameter or fallback also replaces valid falsy values such as zero, empty strings, empty containers and objects with custom truthiness. This opt-in review recognizes two-operand or expressions whose first operand is a directly named, unrebound parameter with a literal None default and a syntactic optional annotation (including quoted unions and typing Optional/Union/Annotated). It skips nested scopes, boolean tests, and parameters rebound anywhere in their function. Intentional falsy defaults remain valid; suppress or disable the review there. This is bounded syntax analysis, not type or control-flow inference.",
        bad="def create(options: Options | None = None):\n    self.options = options or Options()",
        good="def create(options: Options | None = None):\n    self.options = options if options is not None else Options()",
        fix="Review whether only None should select the fallback. If so, use parameter if parameter is not None else fallback. smelt check --select SMT321 --fix --unsafe-fixes applies this behavior-changing rewrite only to visible findings. Plain --fix leaves it unchanged. The rewrite preserves fallback laziness and source outside each expression; inline comments or explicit line continuations within an expression require manual editing. Intentional truthiness fallback should remain unchanged.",
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            syntax = ctx.syntax.for_path(source.path)
            if syntax is None:
                continue
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
                    expected={"parameter": candidate.parameter, "fix_safety": "unsafe"},
                    hint="Use an explicit is not None conditional if only None should select the fallback; review intentional falsy defaults.",
                )
