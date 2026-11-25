from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


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
