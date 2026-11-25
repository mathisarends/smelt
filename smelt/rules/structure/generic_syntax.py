from smelt.analysis.context import Index
from smelt.diagnostics.fixes import (
    FixAvailability,
)
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class PreferInlineTypeParameters(Rule):
    code = "TYP008"
    name = "typing/prefer-inline-type-parameters"
    category = Category.TYPING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="A generic class uses an explicit Generic[...] base on a Python 3.12+ target.",
        rationale=(
            "PEP 695 puts type parameters on the class declaration and scopes "
            "them to the class. This opt-in rule reports parameterized Generic "
            "bases resolved through stable, direct module imports from typing or "
            "typing_extensions, including aliases. It uses project.python_version "
            "or the owning project's requires-python. A minimum below 3.12 or "
            "an unknown minimum leaves legacy syntax valid. Generic subclasses "
            "without an explicit Generic base, functions and type aliases are "
            "outside this check. Ruff UP046 covers similar syntax. Use this "
            "rule to apply Smelt's version and package policy."
        ),
        bad=(
            "from typing import Generic, TypeVar\n\n"
            'T = TypeVar("T")\n\n'
            "class Box(Generic[T]):\n"
            "    value: T"
        ),
        good="class Box[T]:\n    value: T",
        fix=(
            "Declare the parameters on the class, remove the Generic base and "
            "keep any other bases. Preserve parameter order, bounds and "
            "constraints, and check whether the target supports any parameter "
            "defaults. Review inferred variance, shared type variable uses and "
            "runtime introspection before migrating. A class that needs to retain "
            "explicit variance can use a reasoned Smelt suppression. "
            "check --fix --unsafe-fixes migrates classes whose parameters are "
            "module-level TypeVar, ParamSpec or TypeVarTuple declarations without "
            "variance or defaults, and removes declarations and imports nothing "
            "else uses; inferred variance can differ from the declared one. Enable "
            "rules: {typing/prefer-inline-type-parameters: hint}, or select TYP008."
        ),
        canonical=(
            "Declare type parameters inline (class Box[T]:) instead of a "
            "Generic[T] base"
        ),
        config=("rules", "style.overrides", "project.python_version"),
    )
