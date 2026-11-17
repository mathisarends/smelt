from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

from smelt.analysis.bindings import unshadowed_bindings
from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

type _EagerComprehension = ast.ListComp | ast.SetComp | ast.DictComp
type _ExtractableArgument = _EagerComprehension | ast.Call

_AGGREGATES = ("all", "any", "max", "min", "sorted", "sum")

_CONDITIONAL_SCOPES = (
    ast.IfExp,
    ast.BoolOp,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)
_CALLEE_PREVIEW = 60


class ComprehensionInCallArgument(Rule):
    file_local = True
    code = "SMT366"
    name = "comprehension-in-call-argument"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary=(
            "An eager comprehension or builtin reduction is passed directly as a "
            "call argument."
        ),
        rationale=(
            "Naming collection transformations before passing them on separates "
            "calculation from object construction or consumption. This opt-in syntax "
            "review reports list, set and dict comprehensions used as direct positional "
            "or named keyword arguments in production code, once per argument. "
            "It applies to any call without inferring constructors or runtime types. "
            "Simple mappings are included even when SMT363 accepts their internal "
            "logic. A direct argument that reduces a generator expression with an "
            "unshadowed builtin all, any, max, min, sorted or sum is reported as one "
            "transformation; the whole reduction is named and the generator stays "
            "lazy. Named collections, other calls and attribute reads, generator "
            "expressions passed directly, starred arguments and comprehensions "
            "nested inside another argument expression are outside scope. "
            "Calls within lambdas, "
            "comprehensions, conditional expressions, boolean operators, chained "
            "comparisons, assert messages and while conditions are skipped because "
            "extraction can change when or how often they run. Tests are excluded."
        ),
        bad=(
            "def to_response(result):\n"
            "    return Response(\n"
            "        users=[to_user(user) for user in result.users],\n"
            "        active=sum(user.active for user in result.users),\n"
            "    )"
        ),
        good=(
            "def to_response(result):\n"
            "    users = [to_user(user) for user in result.users]\n"
            "    active = sum(user.active for user in result.users)\n"
            "    return Response(users=users, active=active)"
        ),
        fix=(
            "Assign the comprehension or builtin reduction to a local variable "
            "before the call and pass that variable. A named keyword supplies a "
            "suggested variable name. "
            "Preserve the callable lookup, argument evaluation order, guards and "
            "suspension points: moving an argument before the call can evaluate it "
            "before earlier arguments or the callable itself. Do not eagerly "
            "materialize generator expressions. No automatic rewrite is performed. "
            "Enable rules: {comprehension-in-call-argument: hint} or select SMT366. "
            "Package overrides and reasoned inline suppressions are supported."
        ),
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            bindings = unshadowed_bindings(tree, builtin_names=_AGGREGATES)
            aggregates = frozenset(
                name for name in _AGGREGATES if bindings.get(name) == f"builtins.{name}"
            )
            for argument in _comprehension_arguments(tree, aggregates):
                expression = argument.expression
                keyword = argument.keyword
                callee = ast.unparse(argument.call.func)
                if len(callee) > _CALLEE_PREVIEW:
                    callee = callee[: _CALLEE_PREVIEW - 3] + "..."
                variable = keyword if keyword and keyword.isidentifier() else None
                subject = (
                    f"the {argument.aggregate}() result"
                    if argument.aggregate
                    else "the comprehension"
                )
                hint = (
                    f"Assign {subject} to `{variable}` before the call "
                    f"and pass `{variable}={variable}`; preserve evaluation order."
                    if variable
                    else f"Assign {subject} to a named local variable before "
                    "the call and pass that variable; preserve evaluation order."
                )
                yield self.violation(
                    f"Name the collection transformation before passing it to {callee}()",
                    path=source.path,
                    line=expression.lineno,
                    column=expression.col_offset + 1,
                    end_line=expression.end_lineno,
                    end_column=(expression.end_col_offset or 0) + 1,
                    source_module=source.module,
                    expected={
                        "call": callee,
                        "argument": keyword,
                        "comprehension_kind": argument.kind,
                        "aggregate": argument.aggregate,
                        "suggested_variable": variable,
                        "automatic_rewrite": False,
                    },
                    hint=hint,
                )


@dataclass(frozen=True, slots=True)
class _ComprehensionArgument:
    call: ast.Call
    expression: _ExtractableArgument
    keyword: str | None

    @property
    def aggregate(self) -> str | None:
        expression = self.expression
        if isinstance(expression, ast.Call) and isinstance(expression.func, ast.Name):
            return expression.func.id
        return None

    @property
    def kind(self) -> str:
        expression = self.expression
        if isinstance(expression, ast.Call):
            return type(expression.args[0]).__name__
        return type(expression).__name__


def _comprehension_arguments(
    node: ast.AST, aggregates: frozenset[str]
) -> Iterator[_ComprehensionArgument]:
    if isinstance(node, _CONDITIONAL_SCOPES) or (
        isinstance(node, ast.Compare) and len(node.ops) > 1
    ):
        return
    if isinstance(node, ast.Call):
        for argument in node.args:
            if _is_extractable(argument, aggregates):
                yield _ComprehensionArgument(node, argument, None)
        for keyword in node.keywords:
            if keyword.arg is not None and _is_extractable(keyword.value, aggregates):
                yield _ComprehensionArgument(node, keyword.value, keyword.arg)
    for child in ast.iter_child_nodes(node):
        if isinstance(node, ast.While) and child is node.test:
            continue
        if isinstance(node, ast.Assert) and child is node.msg:
            continue
        yield from _comprehension_arguments(child, aggregates)


def _is_extractable(
    node: ast.expr, aggregates: frozenset[str]
) -> TypeGuard[_ExtractableArgument]:
    if isinstance(node, ast.ListComp | ast.SetComp | ast.DictComp):
        return True
    return _is_aggregate_reduction(node, aggregates)


def _is_aggregate_reduction(node: ast.expr, aggregates: frozenset[str]) -> bool:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in aggregates
        and node.args
    ):
        return False
    generator = node.args[0]
    return (
        isinstance(generator, ast.GeneratorExp)
        and not any(isinstance(arg, ast.Starred) for arg in node.args)
        and not any(keyword.arg is None for keyword in node.keywords)
        and not any(comprehension.is_async for comprehension in generator.generators)
    )
