from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.enum_values import str_enum_value_accesses
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class RedundantStrEnumValue(BaseRule):
    code = "SMT318"
    name = "redundant-str-enum-value"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Review .value on a known StrEnum member for direct use of the member.",
        rationale="StrEnum members are already strings, so extracting .value often adds noise and loses the enum type unnecessarily. This opt-in hint recognizes direct Class.MEMBER.value reads on analyzed, top-level classes inheriting directly from enum.StrEnum, including import aliases, relative imports and analyzed re-exports. Simple string, auto() and member-alias declarations are supported. Ordinary Enum, str/Enum mixins, unknown or shadowed bindings, custom enum construction/value access, instance variables and dynamic lookups are excluded. Shadowing anywhere in a module conservatively limits recognition there.",
        bad='from enum import StrEnum\n\nclass MouseEventKind(StrEnum):\n    WHEEL = "mouseWheel"\n\ndispatch_mouse_event(type=MouseEventKind.WHEEL.value)',
        good='from enum import StrEnum\n\nclass MouseEventKind(StrEnum):\n    WHEEL = "mouseWheel"\n\ndispatch_mouse_event(type=MouseEventKind.WHEEL)',
        fix="Prefer the member directly when the consumer accepts string subclasses. Review boundary behavior first: exact str checks, identity, repr and serialization contracts can distinguish the member from its raw value. Keep .value or an explicit str(member) conversion where a plain string is intentional, and suppress the hint with a reason. No automatic rewrite is performed; analyzed code is never executed.",
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for access in str_enum_value_accesses(ctx.syntax):
            replacement = ast.unparse(access.node.value)
            yield self.violation(
                f"consider {replacement} directly; StrEnum members are already strings",
                path=access.path,
                line=access.node.lineno,
                column=access.node.col_offset + 1,
                end_line=access.node.end_lineno,
                end_column=(access.node.end_col_offset + 1)
                if access.node.end_col_offset is not None
                else None,
                source_module=access.module,
                expected={
                    "enum": access.enum,
                    "member": access.member,
                    "replacement": replacement,
                    "automatic_rewrite": False,
                },
                hint=f"Use {replacement} if the consumer accepts string subclasses; retain an explicit conversion when it requires an exact str.",
            )
