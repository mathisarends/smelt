from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.typing_bindings import typing_bindings, typing_name
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

_INLINE_TYPE_PARAMETERS = (3, 12)


class PreferInlineTypeParameters(Rule):
    file_local = True
    code = "TYP008"
    name = "typing/prefer-inline-type-parameters"
    category = Category.TYPING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
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
            "explicit variance can use a reasoned Smelt suppression. Enable "
            "rules: {typing/prefer-inline-type-parameters: hint}, or select TYP008."
        ),
        canonical=(
            "Declare type parameters inline (class Box[T]:) instead of a "
            "Generic[T] base"
        ),
        config=("rules", "style.overrides", "project.python_version"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            target = ctx.python_targets.for_path(source.absolute)
            if target.minimum is None or target.minimum < _INLINE_TYPE_PARAMETERS:
                continue
            tree = ctx.asts.parse(source.path)
            bindings = typing_bindings(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef) or not any(
                    isinstance(base, ast.Subscript)
                    and typing_name(base.value, bindings) == "Generic"
                    for base in node.bases
                ):
                    continue
                yield self.violation(
                    f"declare type parameters inline on class {node.name}",
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "class": node.name,
                        "python": target.to_json(),
                        "automatic_rewrite": False,
                    },
                    hint=(
                        "Use PEP 695 class type parameters in place of the Generic "
                        "base after checking inferred variance and shared type variable uses."
                    ),
                )
