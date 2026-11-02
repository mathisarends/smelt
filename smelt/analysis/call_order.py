"""Bounded caller-before-callee review within one definition namespace."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.graphs import strongly_connected_components

if TYPE_CHECKING:
    from collections.abc import Iterator

type Function = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class CallOrderIssue:
    node: Function
    scope: str
    callees: tuple[tuple[str, int], ...]


def call_order_issues(tree: ast.Module) -> Iterator[CallOrderIssue]:
    yield from _scope_issues(tree.body, "module", methods=False)
    for node in tree.body:
        if (
            isinstance(node, ast.ClassDef)
            and not node.bases
            and not node.decorator_list
        ):
            yield from _scope_issues(node.body, node.name, methods=True)


def _scope_issues(
    body: list[ast.stmt], scope: str, *, methods: bool
) -> Iterator[CallOrderIssue]:
    bound = _bindings(body)
    if bound["*"]:
        return
    functions = {
        node.name: node
        for node in body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and bound[node.name] == 1
        and not node.decorator_list
        and not node.name.startswith("__")
    }
    graph = {
        name: _callees(node, methods=methods) & functions.keys()
        for name, node in functions.items()
    }
    components = {
        name: index
        for index, group in enumerate(strongly_connected_components(graph))
        for name in group
    }
    for name, caller in functions.items():
        prerequisites = {
            node.id
            for root in [
                *caller.args.defaults,
                *(value for value in caller.args.kw_defaults if value is not None),
                *(
                    arg.annotation
                    for arg in [
                        *caller.args.posonlyargs,
                        *caller.args.args,
                        *caller.args.kwonlyargs,
                    ]
                    if arg.annotation is not None
                ),
                *([caller.returns] if caller.returns else []),
            ]
            for node in ast.walk(root)
            if isinstance(node, ast.Name)
        }
        earlier = tuple(
            sorted(
                (callee, functions[callee].lineno)
                for callee in graph[name]
                if functions[callee].lineno < caller.lineno
                and components[callee] != components[name]
                and callee not in prerequisites
                and callee.startswith("_") == name.startswith("_")
            )
        )
        if earlier:
            yield CallOrderIssue(caller, scope, earlier)


def _callees(node: Function, *, methods: bool) -> set[str]:
    local = _bindings(node.body)
    local.update(
        arg.arg
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    )
    if node.args.vararg:
        local[node.args.vararg.arg] += 1
    if node.args.kwarg:
        local[node.args.kwarg.arg] += 1
    receiver = node.args.args[0].arg if methods and node.args.args else None
    receiver_rebound = receiver is not None and local[receiver] != 1
    result: set[str] = set()
    for statement in node.body:
        for child in _body_nodes(statement):
            if not isinstance(child, ast.Call):
                continue
            if (
                not methods
                and isinstance(child.func, ast.Name)
                and not local[child.func.id]
            ):
                result.add(child.func.id)
            elif (
                methods
                and not receiver_rebound
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == receiver
            ):
                result.add(child.func.attr)
    return result


def _bindings(body: list[ast.stmt]) -> Counter[str]:
    result: Counter[str] = Counter()
    for statement in body:
        for node in _body_nodes(statement, include_definitions=True):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
                result[node.id] += 1
            elif isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                result[node.name] += 1
            elif isinstance(node, ast.Import | ast.ImportFrom):
                result.update(
                    alias.asname or alias.name.split(".")[0] for alias in node.names
                )
            elif isinstance(node, ast.Global | ast.Nonlocal):
                result.update(node.names)
            elif (
                isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar)
                and node.name
            ):
                result[node.name] += 1
            elif isinstance(node, ast.MatchMapping) and node.rest:
                result[node.rest] += 1
    return result


def _body_nodes(
    node: ast.AST, *, include_definitions: bool = False
) -> Iterator[ast.AST]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        if include_definitions:
            yield node
        return
    if isinstance(
        node, ast.Lambda | ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _body_nodes(child, include_definitions=include_definitions)
