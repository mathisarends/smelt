from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import tree_nodes
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class NestedConditionalExpression(Rule):
    code = "CPX005"
    name = "complexity/forbidden-nested-conditional"
    category = Category.COMPLEXITY
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A conditional expression directly contains another result conditional.",
        rationale=(
            "Nested conditional expressions hide a multi-branch decision inside "
            "one expression. This opt-in style review reports a conditional whose "
            "body or else branch is another conditional expression, once at the "
            "outermost expression in each directly connected chain. Conditionals "
            "inside tests, calls, lambdas or comprehensions are separate expressions; "
            "their presence alone does not make the enclosing conditional nested. "
            "Simple fallbacks and independent conditionals remain valid. Only "
            "production source is checked; no type or runtime inference is used."
        ),
        bad="status = OK if completed else TIMEOUT if timed_out else ERROR",
        good=(
            "if completed:\n"
            "    status = OK\n"
            "elif timed_out:\n"
            "    status = TIMEOUT\n"
            "else:\n"
            "    status = ERROR"
        ),
        fix=(
            "Use explicit if/elif or match statements. Preserve branch order, "
            "precedence and lazy evaluation; an eagerly evaluated dictionary can "
            "change behavior. No automatic rewrite is performed. Enable "
            "rules: {complexity/forbidden-nested-conditional: hint}, or select CPX005. "
            "Package overrides and reasoned suppressions are supported."
        ),
        canonical=(
            "Write nested decisions as if/elif or match statements, not "
            "nested conditional expressions"
        ),
        config=("rules", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            expressions = [
                node for node in tree_nodes(tree) if isinstance(node, ast.IfExp)
            ]
            nested = {
                branch
                for node in expressions
                for branch in (node.body, node.orelse)
                if isinstance(branch, ast.IfExp)
            }
            for node in expressions:
                if node in nested or not any(
                    isinstance(branch, ast.IfExp) for branch in (node.body, node.orelse)
                ):
                    continue
                yield self.violation(
                    "This conditional expression contains multiple decision branches; "
                    "use explicit if/elif or match statements",
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    end_line=node.end_lineno,
                    end_column=(node.end_col_offset or 0) + 1,
                    source_module=source.module,
                    expected={"automatic_rewrite": False},
                    hint="Preserve lazy branch evaluation and branch order in the statements.",
                )
