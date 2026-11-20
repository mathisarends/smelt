from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

from smelt.analysis.bindings import unshadowed_bindings
from smelt.analysis.context import AnalysisContext, Index
from smelt.config.models import POLICY_DEFAULTS
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import SourceFile

type _EagerComprehension = ast.ListComp | ast.SetComp | ast.DictComp
type _ExtractableArgument = _EagerComprehension | ast.Call

_AGGREGATES = ("all", "any", "max", "min", "sorted", "sum")

# Extracting an argument out of these would change when or how often it runs.
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


class InlineCallArgument(Rule):
    file_local = True
    activation = POLICIES["CPX002"]
    code = "CPX002"
    name = "complexity/inline-call-argument"
    category = Category.COMPLEXITY
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary=(
            "An awaited result, eager comprehension or builtin reduction is passed "
            "directly as a call argument."
        ),
        rationale=(
            "Naming an intermediate value before passing it on separates the "
            "suspension point or collection transformation from the construction "
            "or call that consumes it, and gives the value a name in tracebacks and "
            "debuggers. This opt-in syntax review reports a direct positional or "
            "named keyword argument of a call in production code that is one of "
            "the kinds in call_argument_kinds (all by default): await "
            "(an await expression, including nested forms such as "
            "await f(await g())), comprehension (a list, set or dict "
            "comprehension, even when CPX006 accepts its internal logic) or "
            "aggregate (an unshadowed builtin all, any, max, min, sorted or sum "
            "reducing a generator expression; the whole reduction is named and the "
            "generator stays lazy). It applies to any call without inferring "
            "constructors or runtime types and reports each argument once. Plain "
            "awaits, return await, named values, other calls and attribute reads, "
            "generator expressions passed directly, starred arguments and values "
            "nested inside another argument expression are outside scope. "
            "Arguments within lambdas, comprehensions, conditional expressions, "
            "boolean operators, while conditions, chained comparisons and assert "
            "messages are skipped because extraction would change when or how "
            "often they run. Tests are excluded."
        ),
        bad=(
            "async def to_response(browser, result):\n"
            "    return Response(\n"
            "        text=await browser.clipboard.read(),\n"
            "        users=[to_user(user) for user in result.users],\n"
            "        active=sum(user.active for user in result.users),\n"
            "    )"
        ),
        good=(
            "async def to_response(browser, result):\n"
            "    text = await browser.clipboard.read()\n"
            "    users = [to_user(user) for user in result.users]\n"
            "    active = sum(user.active for user in result.users)\n"
            "    return Response(text=text, users=users, active=active)"
        ),
        fix=(
            "Assign the value to a local variable in the statement before the call "
            "and pass that variable; a named keyword supplies a suggested variable "
            "name. Preserve the callable lookup, argument evaluation order, guards "
            "and suspension points: moving an argument before the call can "
            "evaluate it before earlier arguments or the callable itself. Do not "
            "eagerly materialize generator expressions. No automatic rewrite is "
            "performed. Enable rules: {complexity/inline-call-argument: hint}, "
            "select CPX002, or set call_argument_kinds under style or in a "
            "style override, which also narrows the review to that scope. "
            "Reasoned inline suppressions are supported."
        ),
        config=("rules", "style.call_argument_kinds", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            contract = ctx.contracts.policy_for(source.module, "call_argument_kinds")
            kinds = frozenset(
                contract.spec.call_argument_kinds
                if contract and contract.spec.call_argument_kinds
                else POLICY_DEFAULTS.call_argument_kinds
            )
            tree = ctx.asts.parse(source.path)
            if "await" in kinds:
                for call, awaited, keyword in _awaited_arguments(
                    tree, conditional=False
                ):
                    yield self._awaited(source, call, awaited, keyword=keyword)
            if kinds & {"comprehension", "aggregate"}:
                bindings = unshadowed_bindings(tree, builtin_names=_AGGREGATES)
                aggregates = (
                    frozenset(
                        name
                        for name in _AGGREGATES
                        if bindings.get(name) == f"builtins.{name}"
                    )
                    if "aggregate" in kinds
                    else frozenset()
                )
                for argument in _comprehension_arguments(tree, aggregates):
                    if argument.aggregate or "comprehension" in kinds:
                        yield self._transformation(source, argument)

    def _awaited(
        self,
        source: SourceFile,
        call: ast.Call,
        awaited: ast.Await,
        *,
        keyword: str | None,
    ) -> Violation:
        callee = _callee(call)
        variable = keyword if keyword and keyword.isidentifier() else None
        hint = (
            f"Assign `{variable} = {ast.unparse(awaited)}` before the call "
            f"and pass `{variable}={variable}`."
            if variable
            else "Assign the awaited result to a named local variable "
            "before the call and pass that variable."
        )
        return self.violation(
            f"name the awaited result before passing it to {callee}()",
            path=source.path,
            line=awaited.lineno,
            column=awaited.col_offset + 1,
            end_line=awaited.end_lineno,
            end_column=(awaited.end_col_offset or awaited.col_offset) + 1,
            source_module=source.module,
            expected={
                "kind": "await",
                "call": callee,
                "argument": keyword,
                "awaited": ast.unparse(awaited.value),
                "suggested_variable": variable,
                "automatic_rewrite": False,
            },
            hint=hint,
        )

    def _transformation(
        self, source: SourceFile, argument: _ComprehensionArgument
    ) -> Violation:
        expression = argument.expression
        keyword = argument.keyword
        callee = _callee(argument.call)
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
        return self.violation(
            f"Name the collection transformation before passing it to {callee}()",
            path=source.path,
            line=expression.lineno,
            column=expression.col_offset + 1,
            end_line=expression.end_lineno,
            end_column=(expression.end_col_offset or 0) + 1,
            source_module=source.module,
            expected={
                "kind": "aggregate" if argument.aggregate else "comprehension",
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


def _callee(call: ast.Call) -> str:
    callee = ast.unparse(call.func)
    if len(callee) > _CALLEE_PREVIEW:
        return callee[: _CALLEE_PREVIEW - 3] + "..."
    return callee


def _awaited_arguments(
    node: ast.AST, *, conditional: bool
) -> Iterator[tuple[ast.Call, ast.Await, str | None]]:
    if isinstance(node, ast.Call) and not conditional:
        for argument in node.args:
            if isinstance(argument, ast.Await):
                yield node, argument, None
        for keyword in node.keywords:
            if isinstance(keyword.value, ast.Await) and keyword.arg is not None:
                yield node, keyword.value, keyword.arg
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            nested = False
        else:
            nested = (
                conditional
                or isinstance(node, _CONDITIONAL_SCOPES)
                or (isinstance(node, ast.While) and child is node.test)
            )
        yield from _awaited_arguments(child, conditional=nested)


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
