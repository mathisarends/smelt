from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

type _Function = ast.FunctionDef | ast.AsyncFunctionDef

_EXEMPT_DECORATORS = frozenset({"overload", "override"})


class ExcessPositionalParameters(Rule):
    file_local = True
    activation = POLICIES["SMT345"]
    code = "SMT345"
    name = "excess-positional-parameters"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A function accepts more positional parameters than its package allows.",
        rationale=(
            "Call sites such as connect(user, run, tab, True) do not say which "
            "value is which, and swapping two arguments of the same type still "
            "type-checks. A package contract sets max_positional_parameters; "
            "parameters after that limit must be keyword-only so every call names "
            "them. self and cls are not counted, nor are *args and **kwargs. "
            "Dunder methods, @overload and @override definitions are exempt "
            "because their signatures are dictated elsewhere, and tests are not "
            "checked. Positional-only parameters count like any other positional "
            "parameter."
        ),
        bad=(
            "def connect(user_id: UUID, run_id: UUID, tab_id: UUID) -> None:\n"
            "    ..."
        ),
        good=(
            "def connect(user_id: UUID, *, run_id: UUID, tab_id: UUID) -> None:\n"
            "    ..."
        ),
        fix=(
            "Insert * after the last allowed positional parameter, then pass the "
            "remaining arguments by keyword at every call site. Callbacks whose "
            "positional signature is fixed by a framework keep it with a reasoned "
            "suppression. No automatic rewrite is performed. Enable it with a "
            "package contract, e.g. structure.contracts.<name>: {match: app, "
            "max_positional_parameters: 2}."
        ),
        config=("structure.contracts",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            contract = ctx.contracts.policy_for(
                source.module, "max_positional_parameters"
            )
            if contract is None or contract.spec.max_positional_parameters is None:
                continue
            limit = contract.spec.max_positional_parameters
            for function in _functions(ctx.asts.parse(source.path)):
                if len(function.positional) <= limit:
                    continue
                excess = function.positional[limit:]
                anchor = (
                    f"after {function.positional[limit - 1]}"
                    if limit
                    else "before the first parameter"
                )
                yield self.violation(
                    f"{function.node.name} accepts {len(function.positional)} "
                    f"positional parameters; {contract.name} allows {limit}",
                    path=source.path,
                    line=function.node.lineno,
                    column=function.node.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "contract": contract.name,
                        "max_positional_parameters": limit,
                        "function": function.node.name,
                        "positional": function.positional,
                        "keyword_only_candidates": excess,
                        "automatic_rewrite": False,
                    },
                    hint=(
                        f"Make {', '.join(excess)} keyword-only by placing * "
                        f"{anchor}; pass them by keyword at call sites."
                    ),
                )


@dataclass(frozen=True)
class _Signature:
    node: _Function
    positional: list[str]


def _functions(tree: ast.Module) -> Iterator[_Signature]:
    yield from _walk(tree.body, in_class=False)


def _walk(body: list[ast.stmt], *, in_class: bool) -> Iterator[_Signature]:
    for node in body:
        match node:
            case ast.ClassDef():
                yield from _walk(node.body, in_class=True)
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                if signature := _signature(node, in_class=in_class):
                    yield signature
                yield from _walk(node.body, in_class=False)
            case _:
                yield from _walk(_nested(node), in_class=in_class)


def _nested(node: ast.stmt) -> list[ast.stmt]:
    return [
        child
        for field in ("body", "orelse", "finalbody", "handlers", "cases")
        for item in getattr(node, field, ())
        for child in (item.body if isinstance(item, ast.ExceptHandler | ast.match_case) else [item])
        if isinstance(child, ast.stmt)
    ]


def _signature(node: _Function, *, in_class: bool) -> _Signature | None:
    if node.name.startswith("__") and node.name.endswith("__"):
        return None
    decorators = {_decorator_name(decorator) for decorator in node.decorator_list}
    if decorators & _EXEMPT_DECORATORS:
        return None
    names = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args)]
    if in_class and "staticmethod" not in decorators and names:
        names = names[1:]
    return _Signature(node, names)


def _decorator_name(decorator: ast.expr) -> str | None:
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    return None
