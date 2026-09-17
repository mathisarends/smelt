from __future__ import annotations

import ast
from typing import TYPE_CHECKING, ClassVar

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.code.common import node_violation, parse_annotation, scopes

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.syntax import ModuleSyntax
    from smelt.rules.code.common import Scope


def _decorators(scope: Scope) -> set[str]:
    names: set[str] = set()
    for decorator in scope.function.decorator_list:
        match decorator:
            case ast.Name(id=name) | ast.Attribute(attr=name):
                names.add(name)
    return names


def _names_class(annotation: ast.expr | None, name: str) -> bool:
    match annotation:
        case ast.Name(id=found):
            return found == name
        case ast.Constant(value=str() as text):
            return _names_class(parse_annotation(text), name)
        case _:
            return False


class SelfClassReference(BaseRule):
    code = "SMT206"
    name = "self-class-reference"
    category = Category.CODE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    enabled_by_default: ClassVar[bool] = False
    doc = RuleDoc(
        summary=(
            "A method constructs or returns its own class by name instead of "
            "cls(...)/type(self)(...) or Self."
        ),
        rationale=(
            "Hard-coding the class name breaks subclasses: Money.add returns Money even "
            "when called on Euro. cls, type(self) and Self follow the actual type."
        ),
        bad=(
            "class Money:\n"
            '    def add(self, other: "Money") -> "Money":\n'
            "        return Money(self.cents + other.cents)"
        ),
        good=(
            "class Money:\n"
            "    def add(self, other: Self) -> Self:\n"
            "        return type(self)(self.cents + other.cents)"
        ),
        fix="Use cls(...) in classmethods, type(self)(...) in methods and Self in annotations.",
        config=("rules.SMT206",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for syntax in ctx.syntax.sources():
            for scope in scopes(syntax.tree):
                if scope.cls is None:
                    continue
                yield from self._check_scope(ctx, syntax, scope, scope.cls.name)

    def _check_scope(
        self, ctx: AnalysisContext, syntax: ModuleSyntax, scope: Scope, name: str
    ) -> Iterator[Violation]:
        decorators = _decorators(scope)
        if "staticmethod" in decorators:
            return
        function = scope.function
        if _names_class(function.returns, name) and function.returns is not None:
            yield node_violation(
                self,
                ctx,
                syntax,
                function.returns,
                f"{scope.qualname} is annotated to return {name}; use Self",
                hint="Annotate with typing.Self so subclasses get their own type.",
            )
        replacement = "cls" if "classmethod" in decorators else "type(self)"
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name
            ):
                yield node_violation(
                    self,
                    ctx,
                    syntax,
                    node.func,
                    f"{scope.qualname} constructs {name}(...); use {replacement}(...)",
                    hint=f"Replace {name}(...) with {replacement}(...).",
                )
