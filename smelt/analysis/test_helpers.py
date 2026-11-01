"""Bounded, same-file helper bindings and explicit coroutine execution."""

from __future__ import annotations

import ast
from collections import Counter
from typing import TYPE_CHECKING

from smelt.analysis.test_deadlines import async_deadline

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

type Function = ast.FunctionDef | ast.AsyncFunctionDef


def local_helpers(tree: ast.Module) -> dict[int, Function]:
    """Only unconditional, undecorated, non-generator, unambiguous definitions."""
    result: dict[int, Function] = {}
    dynamic_writes = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Global | ast.Nonlocal)
        for name in node.names
    }
    for scope in ast.walk(tree):
        if not isinstance(scope, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        bindings = _scope_bindings(scope)
        if bindings["*"]:
            continue
        unstable = {name for name, count in bindings.items() if count > 1}
        for node in scope.body:
            if (
                not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                or node.name.startswith("test_")
                or node.decorator_list
                or bindings[node.name] != 1
                or node.name in dynamic_writes
            ):
                continue
            body = list(_scope_nodes(node.body))
            if any(isinstance(child, ast.Yield | ast.YieldFrom) for child in body):
                continue
            if any(
                isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Load)
                and child.id in unstable | dynamic_writes
                for child in body
            ):
                continue
            result[node.lineno] = node
    return result


def helper_executes(
    helper: Function,
    call: ast.Call,
    calls: Mapping[ast.Call, str],
    parents: Mapping[ast.AST, ast.AST],
) -> bool:
    if isinstance(helper, ast.FunctionDef):
        return True
    if async_deadline(call, calls, parents).awaited:
        return True
    parent = parents.get(call)
    return (
        isinstance(parent, ast.Call)
        and calls.get(parent) == "asyncio.run"
        and bool(parent.args)
        and parent.args[0] is call
    )


def outcome_propagates(
    node: ast.AST,
    parents: Mapping[ast.AST, ast.AST],
    calls: Mapping[ast.Call, str],
) -> bool:
    child = node
    while child in parents:
        parent = parents[child]
        if isinstance(parent, ast.Try | ast.TryStar):
            return False
        if (
            isinstance(parent, ast.With | ast.AsyncWith)
            and child in parent.body
            and any(
                not isinstance(item.context_expr, ast.Call)
                or calls.get(item.context_expr)
                not in {
                    "asyncio.timeout",
                    "asyncio.timeout_at",
                    "anyio.fail_after",
                    "trio.fail_after",
                }
                for item in parent.items
            )
        ):
            return False
        child = parent
    return True


def _scope_bindings(scope: ast.Module | Function) -> Counter[str]:
    bindings: Counter[str] = Counter()
    for node in _scope_nodes(scope.body):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            bindings[node.id] += 1
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bindings[node.name] += 1
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                bindings[alias.asname or alias.name.split(".")[0]] += 1
        elif (isinstance(node, ast.ExceptHandler) and node.name) or (
            isinstance(node, ast.MatchAs | ast.MatchStar) and node.name
        ):
            bindings[node.name] += 1
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bindings[node.rest] += 1
    if isinstance(scope, ast.FunctionDef | ast.AsyncFunctionDef):
        for argument in ast.walk(scope.args):
            if isinstance(argument, ast.arg):
                bindings[argument.arg] += 1
    return bindings


def _scope_nodes(nodes: list[ast.stmt]) -> Iterator[ast.AST]:
    pending: list[ast.AST] = list(nodes)
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
        ):
            pending.extend(ast.iter_child_nodes(node))
