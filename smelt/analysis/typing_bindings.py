"""Conservative identities for direct, unshadowed typing imports."""

from __future__ import annotations

import ast
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_TYPING_MODULES = frozenset({"typing", "typing_extensions"})


def typing_bindings(tree: ast.Module) -> dict[str, str]:
    # As with enum value analysis, a shadowed import anywhere in the module is
    # uncertain. Only stable, direct module imports establish typing identities.
    bound = _binding_counts(tree)
    if bound["*"]:
        return {}
    bindings: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name in _TYPING_MODULES and bound[local] == 1:
                    bindings[local] = alias.name
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module in _TYPING_MODULES
        ):
            for alias in node.names:
                local = alias.asname or alias.name
                if bound[local] == 1:
                    bindings[local] = f"{node.module}.{alias.name}"
    return bindings


def typing_name(node: ast.expr, bindings: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        qualified = bindings.get(node.id, "")
    elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        qualified = f"{bindings.get(node.value.id, '')}.{node.attr}"
    else:
        return None
    module, _, name = qualified.rpartition(".")
    return name if module in _TYPING_MODULES else None


def _binding_counts(tree: ast.Module) -> Counter[str]:
    bound: Counter[str] = Counter()
    for node in ast.walk(tree):
        match node:
            case (
                ast.Name(id=name, ctx=ast.Store() | ast.Del())
                | ast.arg(arg=name)
                | ast.ClassDef(name=name)
                | ast.FunctionDef(name=name)
                | ast.AsyncFunctionDef(name=name)
                | ast.TypeVar(name=name)
                | ast.ParamSpec(name=name)
                | ast.TypeVarTuple(name=name)
            ):
                bound[name] += 1
            case ast.Import() | ast.ImportFrom():
                for alias in node.names:
                    bound[alias.asname or alias.name.split(".")[0]] += 1
            case (
                ast.ExceptHandler(name=name)
                | ast.MatchAs(name=name)
                | ast.MatchStar(name=name)
                | ast.MatchMapping(rest=name)
            ) if name is not None:
                bound[name] += 1
            case ast.Global(names=names) | ast.Nonlocal(names=names):
                bound.update(names)
        if isinstance(node, ast.Attribute | ast.Subscript) and isinstance(
            node.ctx, ast.Store | ast.Del
        ):
            root = node.value
            while isinstance(root, ast.Attribute | ast.Subscript):
                root = root.value
            if isinstance(root, ast.Name):
                bound[root.id] += 1
    return bound
