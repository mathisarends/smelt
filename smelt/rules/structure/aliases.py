from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class RedundantNameAlias(Rule):
    code = "IDM004"
    name = "idioms/redundant-name-alias"
    category = Category.IDIOMS
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A module-level name only repeats another class or function name.",
        rationale=(
            "Two names for one class or function make readers and agents ask "
            "whether they differ, and grep finds only half of the uses. Such "
            "aliases are typically compatibility names left behind by a rename or "
            "placeholders for a union that never gained a second member. This "
            "opt-in review reports unconditional module-level X = Y, "
            "X: TypeAlias = Y and type X = Y statements whose right side is a bare "
            "name or dotted attribute that refers to a class or function defined "
            "in the same module, or to an imported CapWords name. Type "
            "expressions such as unions, subscripts and Annotated, values such as "
            "constants, conditional assignments (version or import fallbacks), "
            "class-level names, typing special forms such as Any and tests are not "
            "reported."
        ),
        bad=(
            "class PasswordData(BaseModel):\n"
            "    username: str\n\n"
            "AutofillData = PasswordData"
        ),
        good=(
            "class PasswordData(BaseModel):\n"
            "    username: str\n\n"
            "type AutofillData = PasswordData | CardData"
        ),
        fix=(
            "Use the target name everywhere and remove the alias. Introduce a "
            "shared name once it describes a real type expression, such as a union "
            "with a second member. Keep a deliberate public compatibility name with "
            "a reasoned suppression. No automatic rewrite is performed. Enable "
            "rules: {idioms/redundant-name-alias: hint}, or select IDM004."
        ),
        canonical=(
            "Use the original class or function name instead of a module-level alias"
        ),
        config=("rules",),
    )
