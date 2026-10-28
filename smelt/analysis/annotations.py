"""Conservative evidence for reviewing an annotations future import.

This does not prove removal preserves reflection or framework behavior. Unknown
expressions and forward references retain postponement; callers emit hints only.
"""

from __future__ import annotations

import ast
import builtins

_GENERICS_VERSION = (3, 9)
_UNIONS_VERSION = (3, 10)
_BUILTIN_GENERICS = frozenset({"list", "dict", "tuple", "set", "frozenset", "type"})


def has_postponement_evidence(
    tree: ast.Module, minimum: tuple[int, int] | None = None
) -> bool:
    annotations: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign | ast.arg) and node.annotation is not None:
            annotations.append(node.annotation)
        elif (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.returns is not None
        ):
            annotations.append(node.returns)
    if not annotations:
        return False
    if _runtime_sensitive(tree):
        return True
    available = _bindings(tree)
    for annotation in annotations:
        for node in ast.walk(annotation):
            if isinstance(node, ast.Name):
                if node.id not in vars(builtins) and not any(
                    end < annotation.lineno and name == node.id
                    for name, end in available
                ):
                    return True
            elif isinstance(node, ast.Constant):
                if node.value is not None:
                    return True
            elif not _supported_expression(node, minimum):
                # Calls, generics, unions, attributes and other expressions can
                # depend on runtime protocols or an older supported interpreter.
                return True
    return False


def _supported_expression(node: ast.AST, minimum: tuple[int, int] | None) -> bool:
    if isinstance(node, ast.Subscript):
        return (
            minimum is not None
            and minimum >= _GENERICS_VERSION
            and isinstance(node.value, ast.Name)
            and node.value.id in _BUILTIN_GENERICS
        )
    if isinstance(node, ast.BinOp):
        return (
            minimum is not None
            and minimum >= _UNIONS_VERSION
            and isinstance(node.op, ast.BitOr)
        )
    return isinstance(node, ast.Load | ast.Tuple | ast.BitOr)


def _runtime_sensitive(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Delete):
            return True
        if isinstance(node, ast.Name | ast.Attribute):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in {"__annotations__", "get_type_hints", "TYPE_CHECKING"}:
                return True
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.decorator_list
        ):
            return True
        if isinstance(node, ast.ClassDef) and (
            node.bases or node.keywords or node.decorator_list
        ):
            return True
    return False


def _bindings(tree: ast.Module) -> list[tuple[str, int]]:
    bound: list[tuple[str, int]] = []
    for node in tree.body:
        end = node.end_lineno or node.lineno
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    name = alias.asname or (
                        alias.name.split(".")[0]
                        if isinstance(node, ast.Import)
                        else alias.name
                    )
                    bound.append((name, end))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.append((node.name, end))
        # Assignments may be dynamic aliases rather than types; keep those unknown.
    return bound
