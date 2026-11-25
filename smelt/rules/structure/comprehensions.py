from __future__ import annotations

import ast

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import GuideTarget, Rule, RuleDoc

type _Comprehension = ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp


class ComplexComprehension(Rule):
    code = "CPX006"
    name = "complexity/excess-comprehension-loops"
    category = Category.COMPLEXITY
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
            "rewrite is performed. Enable rules: {complexity/excess-comprehension-loops: hint} or "
            "select CPX006; set max on the entry, e.g. {severity: hint, max: 2}. "
            "The limit defaults to 1; an entry in a style override's rules can "
            "permit intentional flattening."
        ),
        canonical=(
            "Keep comprehensions to the configured number of for/if steps; "
            "write a loop otherwise"
        ),
        config=("rules", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        limit, _ = ctx.contracts.threshold(target.module, "max_comprehension_loops")
        noun = "for clause" if limit == 1 else "for clauses"
        return (
            f"Use at most {limit} {noun} and one simple filter per comprehension; "
            "write a loop or name the per-item decision otherwise"
        )
