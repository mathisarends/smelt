from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class InlineConstructorReceiver(Rule):
    code = "CPX008"
    name = "complexity/inline-constructor-receiver"
    category = Category.COMPLEXITY
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A method is called directly on a newly constructed instance.",
        rationale=(
            "Naming the instance on its own line makes it easier to distinguish "
            "construction from the operation that follows. This opt-in review "
            "checks direct method calls used as the whole value of an assignment, "
            "return or expression statement, including an awaited call. It "
            "recognizes unshadowed top-level class declarations and unshadowed "
            "imports whose final name starts with a capital letter (ignoring "
            "leading underscores). The import convention is a heuristic and can "
            "also match a callable with a class-like name. The rule skips files "
            "with wildcard imports. Bare builtins, unresolved names, lowercase "
            "factory imports, chains of method calls, nested expressions and "
            "test files are outside its scope."
        ),
        bad=(
            "from plugins import PointerTools\n\n"
            "pointer = PointerTools().tool(\n"
            "    PointerTools.drag_at,\n"
            '    description="Press, move, release.",\n'
            ")"
        ),
        good=(
            "from plugins import PointerTools\n\n"
            "pointer_tools = PointerTools()\n"
            "pointer = pointer_tools.tool(\n"
            "    PointerTools.drag_at,\n"
            '    description="Press, move, release.",\n'
            ")"
        ),
        fix=(
            "Assign the constructor call to a descriptive variable before the "
            "statement and use that variable as the method receiver. Review "
            "whether keeping the instance bound affects its lifetime. No "
            "automatic rewrite is performed. Enable "
            "rules: {complexity/inline-constructor-receiver: warning} globally "
            "or in a package override, or select CPX008."
        ),
        canonical=(
            "Bind a newly constructed instance to a name before calling methods on it"
        ),
        config=("rules", "style.overrides"),
    )
