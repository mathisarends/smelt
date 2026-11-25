from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import GuideTarget, Rule, RuleDoc


class ConstructorLogic(Rule):
    code = "CPX007"
    name = "complexity/excess-constructor-decisions"
    category = Category.COMPLEXITY
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A constructor contains more decisions than the configured limit.",
        rationale=(
            "Normalization, validation and derivation become easier to understand "
            "when named separately from object initialization. This opt-in review "
            "counts if, for/async for, while, try/try-star, match, raise, comprehension "
            "and conditional-expression nodes in __init__ and __post_init__ methods. "
            "Each comprehension counts once, in addition to any conditional expressions "
            "inside it; its generator clauses are not counted separately. Nested "
            "function, lambda and class definitions are skipped. Helper calls count "
            "zero. A single guard with raise counts two, so it remains valid at the "
            "default limit of 2. Only production source is checked. __new__ and "
            "other validation methods are outside the scope. This metric is advice, "
            "not proof that a constructor needs refactoring."
        ),
        bad=(
            "class Client:\n"
            "    def __init__(self, values):\n"
            "        for value in values:\n"
            "            if not value.ready:\n"
            "                raise ValueError('not ready')\n"
            "        self.values = values"
        ),
        good=(
            "class Client:\n"
            "    def __init__(self, values):\n"
            "        self.values = validate_values(values)"
        ),
        fix=(
            "Move a cohesive normalization or validation step into a named function "
            "or classmethod and assign its result. Preserve evaluation order, exceptions "
            "and initialization side effects. Do not extract arbitrary fragments merely "
            "to lower a count. No automatic rewrite is performed. Enable rules: "
            "{complexity/excess-constructor-decisions: hint} or select CPX007; "
            "set max on the entry, in the root or a style override's rules. "
            "The limit defaults to 2; 0 forbids any counted decision. Reasoned "
            "suppressions and package off overrides are supported."
        ),
        canonical=(
            "Keep constructor decisions within the limit; move normalization "
            "into a named function"
        ),
        config=("rules", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        limit, _ = ctx.contracts.threshold(target.module, "max_constructor_decisions")
        noun = "decision" if limit == 1 else "decisions"
        return (
            f"Keep constructors to {limit} {noun}; "
            "move normalization into a named function or classmethod"
        )
