from __future__ import annotations

import ast

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.fixes import (
    FixAvailability,
)
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import GuideTarget, Rule, RuleDoc

type _Function = ast.FunctionDef | ast.AsyncFunctionDef


class ExcessPositionalParameters(Rule):
    code = "CPX003"
    name = "complexity/excess-positional-parameters"
    category = Category.COMPLEXITY
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="A function accepts more positional parameters than its package allows.",
        rationale=(
            "Call sites such as connect(user, run, tab, True) do not say which "
            "value is which, and swapping two arguments of the same type still "
            "type-checks. The max of the rule entry is the limit (5 when "
            "omitted); "
            "parameters after that limit must be keyword-only so every call names "
            "them. self and cls are not counted, nor are *args and **kwargs. "
            "Dunder methods, @overload and @override definitions are exempt "
            "because their signatures are dictated elsewhere, and tests are not "
            "checked. Positional-only parameters count like any other positional "
            "parameter."
        ),
        bad=(
            "def connect(user_id: UUID, run_id: UUID, tab_id: UUID) -> None:\n    ..."
        ),
        good=(
            "def connect(user_id: UUID, *, run_id: UUID, tab_id: UUID) -> None:\n"
            "    ..."
        ),
        fix=(
            "Insert * after the last allowed positional parameter, then pass the "
            "remaining arguments by keyword at every call site. Callbacks whose "
            "positional signature is fixed by a framework keep it with a reasoned "
            "suppression. Findings carry the * insertion as a suggestion that "
            "check --diff --suggestions shows and --fix never applies, because "
            "call sites must change too; positional-only parameters and "
            "functions with *args get none. Enable it with "
            "rules: {complexity/excess-positional-parameters: "
            "{severity: warning, max: 3}}; an entry in a style override's rules "
            "refines the limit for that package."
        ),
        canonical=(
            "Keep positional parameters within the package limit; make the "
            "rest keyword-only with *"
        ),
        config=("rules", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        limit, _ = ctx.contracts.threshold(target.module, "max_positional_parameters")
        if limit == 0:
            return "Make every parameter keyword-only with a leading *"
        noun = "parameter" if limit == 1 else "parameters"
        return f"Accept at most {limit} positional {noun}; make the rest keyword-only with *"
