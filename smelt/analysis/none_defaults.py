"""Bounded review of truthiness defaults on explicitly optional parameters."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


@dataclass(frozen=True)
class NoneDefault:
    node: ast.BoolOp
    parameter: str


def none_defaults(
    tree: ast.Module, bindings: Mapping[str, str]
) -> Iterator[NoneDefault]:
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        positional = [*function.args.posonlyargs, *function.args.args]
        pairs = [
            *zip(
                positional[len(positional) - len(function.args.defaults) :],
                function.args.defaults,
                strict=True,
            ),
            *zip(function.args.kwonlyargs, function.args.kw_defaults, strict=True),
        ]
        optional = {
            arg.arg
            for arg, default in pairs
            if isinstance(default, ast.Constant)
            and default.value is None
            and arg.annotation is not None
            and _optional(arg.annotation, bindings)
        }
        if not optional:
            continue
        nodes = [
            node for statement in function.body for node in _scope_nodes(statement)
        ]
        optional.difference_update(
            name for node in nodes for name in _bound_names(node)
        )
        if not optional:
            continue
        for statement in function.body:
            for node in _value_nodes(statement):
                match node:
                    case ast.BoolOp(op=ast.Or(), values=[ast.Name(id=name), _]) if (
                        name in optional
                    ):
                        yield NoneDefault(node, name)


def _qualified(node: ast.expr, bindings: Mapping[str, str]) -> str:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f"{_qualified(node.value, bindings)}.{node.attr}"
    return ""


def _optional(node: ast.expr, bindings: Mapping[str, str]) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _optional(ast.parse(node.value, mode="eval").body, bindings)
        except SyntaxError:
            return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return any(
            (isinstance(part, ast.Constant) and part.value is None)
            or _optional(part, bindings)
            for part in (node.left, node.right)
        )
    if not isinstance(node, ast.Subscript):
        return False
    name = _qualified(node.value, bindings)
    parts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
    if name in {"typing.Annotated", "typing_extensions.Annotated"}:
        return bool(parts) and _optional(parts[0], bindings)
    return name in {"typing.Optional", "typing_extensions.Optional"} or (
        name in {"typing.Union", "typing_extensions.Union"}
        and any(isinstance(part, ast.Constant) and part.value is None for part in parts)
    )


def _scope_nodes(node: ast.AST) -> Iterator[ast.AST]:
    yield node
    if not isinstance(node, _SCOPES):
        for child in ast.iter_child_nodes(node):
            yield from _scope_nodes(child)


def _bound_names(node: ast.AST) -> Iterator[str]:
    match node:
        case ast.Name(id=name, ctx=ast.Store() | ast.Del()):
            yield name
        case (
            ast.FunctionDef(name=name)
            | ast.AsyncFunctionDef(name=name)
            | ast.ClassDef(name=name)
        ):
            yield name
        case ast.Import() | ast.ImportFrom():
            for alias in node.names:
                yield alias.asname or alias.name.split(".")[0]
        case (
            ast.ExceptHandler(name=name)
            | ast.MatchAs(name=name)
            | ast.MatchStar(name=name)
            | ast.MatchMapping(rest=name)
        ) if name is not None:
            yield name
        case ast.Global(names=names) | ast.Nonlocal(names=names):
            yield from names


def _value_nodes(node: ast.AST) -> Iterator[ast.AST]:
    if isinstance(node, _SCOPES):
        return
    yield node
    children: list[ast.AST]
    match node:
        case ast.If() | ast.While():
            children = [*node.body, *node.orelse]
        case ast.IfExp():
            children = [node.body, node.orelse]
        case (
            ast.Assert()
            | ast.UnaryOp(op=ast.Not())
            | ast.BoolOp(op=ast.And())
            | ast.Call(func=ast.Name(id="bool"))
        ):
            return
        case ast.comprehension():
            children = [node.iter]
        case ast.match_case():
            children = list(node.body)
        case _:
            children = list(ast.iter_child_nodes(node))
    for child in children:
        yield from _value_nodes(child)
