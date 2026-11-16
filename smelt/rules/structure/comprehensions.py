from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

type _Comprehension = ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp

_MAX_FILTER_OPERANDS = 2


class ComplexComprehension(Rule):
    file_local = True
    code = "SMT363"
    name = "complex-comprehension"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A comprehension combines too many iteration or decision steps.",
        rationale=(
            "This opt-in syntax review reports comprehensions with too many for "
            "clauses, a conditional element/value with a nontrivial branch, or "
            "multiple filters (including a boolean filter with more than two "
            "operands). Names, attribute reads and constants are trivial branches. "
            "Nested comprehension triggers are grouped at the outermost comprehension. "
            "Simple mapping, a single filter and trivial fallbacks remain valid. "
            "Only production source is checked; no types or runtime costs are inferred."
        ),
        bad="values = [convert(x) if x.ready else fallback(x) for x in items]",
        good="values = [resolve_item(x) for x in items]",
        fix=(
            "Name the per-item decision or use an explicit loop. Preserve generator "
            "laziness, iteration/filter order and conditional evaluation. No automatic "
            "rewrite is performed. Enable rules: {complex-comprehension: hint} or "
            "select SMT363. structure.max_comprehension_loops defaults to 1; a "
            "structure.contracts.<name>.max_comprehension_loops override permits "
            "intentional flattening. Threshold options alone do not enable the rule."
        ),
        config=("rules", "structure.max_comprehension_loops", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            contract = ctx.contracts.policy_for(
                source.module, "max_comprehension_loops"
            )
            limit = ctx.config.structure.max_comprehension_loops
            if (
                contract is not None
                and contract.spec.max_comprehension_loops is not None
            ):
                limit = contract.spec.max_comprehension_loops
            for node in _outermost(ctx.asts.parse(source.path)):
                reasons = _reasons(node, limit)
                if not reasons:
                    continue
                yield self.violation(
                    "Complex comprehension: " + "; ".join(reasons),
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    end_line=node.end_lineno,
                    end_column=(node.end_col_offset or 0) + 1,
                    source_module=source.module,
                    expected={
                        "max_comprehension_loops": limit,
                        "conditions": reasons,
                        "automatic_rewrite": False,
                    },
                    hint="Name the per-item step or use a loop; preserve lazy evaluation and iteration order.",
                )


def _outermost(node: ast.AST) -> Iterator[_Comprehension]:
    if isinstance(node, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
        yield node
        return
    for child in ast.iter_child_nodes(node):
        yield from _outermost(child)


def _reasons(root: _Comprehension, limit: int) -> list[str]:
    reasons: set[str] = set()
    for node in ast.walk(root):
        if not isinstance(
            node, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
        ):
            continue
        if len(node.generators) > limit:
            reasons.add("too many for clauses")
        element = node.value if isinstance(node, ast.DictComp) else node.elt
        if isinstance(element, ast.IfExp) and not all(
            isinstance(branch, ast.Name | ast.Attribute | ast.Constant)
            for branch in (element.body, element.orelse)
        ):
            reasons.add("nontrivial conditional element")
        if any(
            len(generator.ifs) > 1
            or any(
                isinstance(expression, ast.BoolOp)
                and len(expression.values) > _MAX_FILTER_OPERANDS
                for condition in generator.ifs
                for expression in ast.walk(condition)
            )
            for generator in node.generators
        ):
            reasons.add("multiple or compound filters")
    return sorted(reasons)
