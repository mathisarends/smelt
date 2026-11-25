from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class DuplicateFunction(Rule):
    code = "CPX004"
    name = "complexity/duplicate-function"
    category = Category.COMPLEXITY
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A module-level function repeats another function's code.",
        rationale=(
            "Copy-pasted helpers drift: a fix or a changed message lands in one "
            "copy and the others keep the old behavior, and readers cannot tell "
            "whether the difference is intended. This opt-in review compares "
            "module-level functions within each analyzed root package and "
            "reports every function whose parameters, annotations, decorators "
            "and body are identical to another one's, ignoring names, "
            "docstrings, comments and formatting. A body needs at least three "
            "statements, counting nested ones, so trivial one-liners are not "
            "reported. Methods, nested functions, test files and separate root "
            "packages are not compared. Each copy gets its own finding with "
            "the other locations."
        ),
        bad=(
            "# app/tools/keyboard.py\n"
            "def _secret_entered(exact: bool) -> Effect:\n"
            "    if exact:\n"
            '        return Effect("Text entered.")\n'
            '    return Effect("Text entered with a slip.", break_chain=True)\n\n'
            "# app/tools/typing.py\n"
            "def _secret_entered(exact: bool) -> Effect:\n"
            "    if exact:\n"
            '        return Effect("Text entered.")\n'
            '    return Effect("Text entered with a slip.", break_chain=True)'
        ),
        good=(
            "# app/tools/effects.py\n"
            "def secret_entered(exact: bool) -> Effect:\n"
            "    if exact:\n"
            '        return Effect("Text entered.")\n'
            '    return Effect("Text entered with a slip.", break_chain=True)\n\n'
            "# app/tools/keyboard.py and app/tools/typing.py\n"
            "from app.tools.effects import secret_entered"
        ),
        fix=(
            "Keep one copy in a module both callers may import, usually in "
            "their nearest common package, and import it from there. Check "
            "architecture boundaries and import cycles when choosing the "
            "owner. Copies that must evolve independently keep a reasoned "
            "suppression. No automatic rewrite is performed. Enable rules: "
            "{complexity/duplicate-function: warning}, or select CPX004."
        ),
        canonical=(
            "Reuse an existing function instead of copying it; share one copy"
            " from a common module"
        ),
        config=("rules",),
    )
