"""Syntactic candidates for reviewing type dispatch, never automatic conversions."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True)
class DispatchBranch:
    line: int
    types: tuple[str, ...]
    guard: str | None


@dataclass(frozen=True)
class TypeDispatch:
    node: ast.If
    subject: str
    branches: tuple[DispatchBranch, ...]
    scope: str


def _bound_names(node: ast.AST) -> tuple[str, ...]:
    match node:
        case (
            ast.Name(id=name, ctx=ast.Store() | ast.Del())
            | ast.arg(arg=name)
            | ast.FunctionDef(name=name)
            | ast.AsyncFunctionDef(name=name)
            | ast.ClassDef(name=name)
        ):
            return (name,)
        case ast.Import() | ast.ImportFrom():
            return tuple(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        case (
            ast.ExceptHandler(name=name)
            | ast.MatchAs(name=name)
            | ast.MatchStar(name=name)
        ):
            return (name,) if name else ()
        case ast.Global(names=names) | ast.Nonlocal(names=names):
            return tuple(names)
    return ()


def _checker_names(tree: ast.Module) -> set[str]:
    """Reject spellings rebound anywhere in the file rather than guessing scope."""
    bound = Counter(name for node in ast.walk(tree) for name in _bound_names(node))
    if bound["*"]:
        return set()
    names = {"isinstance"} if not bound["isinstance"] else set()
    builtin_modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                if alias.name == "builtins" and bound[name] == 1:
                    names.add(f"{name}.isinstance")
                    builtin_modules.add(name)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module == "builtins"
        ):
            for alias in node.names:
                name = alias.asname or alias.name
                if alias.name == "isinstance" and bound[name] == 1:
                    names.add(name)
    for candidate in ast.walk(tree):
        if (
            isinstance(candidate, ast.Attribute | ast.Subscript)
            and isinstance(candidate.ctx, ast.Store | ast.Del)
            and any(
                isinstance(child, ast.Name) and child.id in builtin_modules
                for child in ast.walk(candidate)
            )
        ):
            return set()
    return names


def _type_names(node: ast.expr) -> tuple[str, ...] | None:
    match node:
        case ast.Name():
            return (node.id,)
        case ast.Attribute(value=value):
            parent = _type_names(value)
            return (
                (f"{parent[0]}.{node.attr}",) if parent and len(parent) == 1 else None
            )
        case ast.Tuple(elts=elements) if elements:
            names: list[str] = []
            for element in elements:
                part = _type_names(element)
                if part is None:
                    return None
                names.extend(part)
            return tuple(names)
        case ast.BinOp(left=left, op=ast.BitOr(), right=right):
            first, second = _type_names(left), _type_names(right)
            return (*first, *second) if first and second else None
        case _:
            return None


def _head(
    test: ast.expr, checkers: set[str]
) -> tuple[str, tuple[str, ...], str | None] | None:
    values = (
        test.values
        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And)
        else [test]
    )
    call = values[0]
    match call:
        case ast.Call(func=func, args=[ast.Name(id=subject), types], keywords=[]) if (
            ast.unparse(func) in checkers
        ):
            pass
        case _:
            return None
    names = _type_names(types)
    if not names or any(
        isinstance(node, ast.NamedExpr)
        and isinstance(node.target, ast.Name)
        and node.target.id == subject
        for node in ast.walk(test)
    ):
        return None
    guard = None
    guards = values[1:]
    if guards:
        guard = ast.unparse(
            guards[0] if len(guards) == 1 else ast.BoolOp(op=ast.And(), values=guards)
        )
    return subject, names, guard


def _elif(node: ast.If) -> ast.If | None:
    if (
        len(node.orelse) == 1
        and isinstance(node.orelse[0], ast.If)
        and node.orelse[0].col_offset == node.col_offset
    ):
        return node.orelse[0]
    return None


def _scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    names: list[str] = []
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.append(node.name)
    return ".".join(["module", *reversed(names)])


def type_dispatches(tree: ast.Module, minimum: int) -> Iterator[TypeDispatch]:
    checkers = _checker_names(tree)
    if not checkers:
        return
    conditions = [node for node in ast.walk(tree) if isinstance(node, ast.If)]
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    tails = {id(tail) for node in conditions if (tail := _elif(node)) is not None}
    for root in conditions:
        if id(root) in tails:
            continue
        node: ast.If | None = root
        branches: list[DispatchBranch] = []
        subject: str | None = None
        supported = True
        while node is not None:
            head = _head(node.test, checkers)
            if head is None or (subject is not None and head[0] != subject):
                supported = False
                break
            subject, types, guard = head
            branches.append(DispatchBranch(node.lineno, types, guard))
            node = _elif(node)
        if supported and subject is not None and len(branches) >= minimum:
            yield TypeDispatch(root, subject, tuple(branches), _scope(root, parents))
