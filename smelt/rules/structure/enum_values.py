from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class RedundantStrEnumValue(Rule):
    code = "MOD001"
    name = "modeling/redundant-str-enum-value"
    category = Category.MODELING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Review .value on a known StrEnum member for direct use of the member.",
        rationale="StrEnum members are already strings, so extracting .value often adds noise and loses the enum type unnecessarily. This opt-in hint recognizes direct Class.MEMBER.value reads on analyzed, top-level classes inheriting directly from enum.StrEnum, including import aliases, relative imports and analyzed re-exports. Simple string, auto() and member-alias declarations are supported. Ordinary Enum, str/Enum mixins, unknown or shadowed bindings, custom enum construction/value access, instance variables and dynamic lookups are excluded. Shadowing anywhere in a module conservatively limits recognition there.",
        bad='from enum import StrEnum\n\nclass MouseEventKind(StrEnum):\n    WHEEL = "mouseWheel"\n\ndispatch_mouse_event(type=MouseEventKind.WHEEL.value)',
        good='from enum import StrEnum\n\nclass MouseEventKind(StrEnum):\n    WHEEL = "mouseWheel"\n\ndispatch_mouse_event(type=MouseEventKind.WHEEL)',
        fix="Prefer the member directly when the consumer accepts string subclasses. Review boundary behavior first: exact str checks, identity, repr and serialization contracts can distinguish the member from its raw value. Literal-typed parameters can also reject the member in a type checker; Smelt has no consumer type information. Keep .value or an explicit str(member) conversion where a plain string is intentional, and suppress the hint with a reason. No automatic rewrite is performed; analyzed code is never executed.",
        canonical="Use a StrEnum member directly instead of its .value",
        config=("rules",),
    )
