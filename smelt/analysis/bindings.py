"""Name resolution helpers shared by syntax-only analyses and rules."""

from __future__ import annotations

import ast
from collections import Counter
from typing import TYPE_CHECKING

from smelt.analysis.exports import namespace_of

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


def unshadowed_bindings(
    tree: ast.Module, *, builtin_names: Iterable[str] = ()
) -> dict[str, str]:
    """Resolve module imports and requested builtins, skipping any shadowed name.

    This is deliberately file-wide: local bindings can suppress unrelated uses.
    Wildcard imports are unknown; relative imports and re-exports are not resolved.
    """
    counts = binding_counts(tree)
    if counts["*"]:
        return {}
    imports = namespace_of(tree, "", is_package=False).imported
    relative = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level
        for alias in node.names
    }
    return {
        **{name: f"builtins.{name}" for name in builtin_names if not counts[name]},
        **{
            name: target
            for name, target in imports.items()
            if counts[name] == 1 and name not in relative
        },
    }


def binding_counts(tree: ast.Module) -> Counter[str]:
    """Count how often each local name is bound anywhere in ``tree``.

    A name bound more than once may shadow an import, so callers treat it as
    unresolved instead of trusting the import.
    """
    bound: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            bound[node.id] += 1
        elif isinstance(node, ast.arg):
            bound[node.arg] += 1
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            bound[node.name] += 1
        elif isinstance(node, ast.Import | ast.ImportFrom):
            bound.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.Global | ast.Nonlocal):
            bound.update(node.names)
        elif (
            isinstance(
                node,
                ast.TypeVar
                | ast.ParamSpec
                | ast.TypeVarTuple
                | ast.ExceptHandler
                | ast.MatchAs
                | ast.MatchStar,
            )
            and node.name
        ):
            bound[node.name] += 1
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound[node.rest] += 1
        elif isinstance(node, ast.Attribute | ast.Subscript) and isinstance(
            node.ctx, ast.Store | ast.Del
        ):
            root = node.value
            while isinstance(root, ast.Attribute | ast.Subscript):
                root = root.value
            if isinstance(root, ast.Name):
                bound[root.id] += 1
    return bound


def annotation_name(node: ast.expr, bindings: Mapping[str, str]) -> str | None:
    """Resolve the outer type of a direct annotation, including a quoted one."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None
    if isinstance(node, ast.Subscript):
        node = node.value
    return qualified_name(node, bindings)


def qualified_name(node: ast.expr, bindings: Mapping[str, str]) -> str | None:
    """Return the imported dotted name ``node`` refers to, if it is known."""
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.Attribute) and (
        parent := qualified_name(node.value, bindings)
    ):
        return f"{parent}.{node.attr}"
    return None


def dotted_name(node: ast.expr) -> str:
    """Spell a name or attribute chain as written, or return "" for other nodes."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (owner := dotted_name(node.value)):
        return f"{owner}.{node.attr}"
    return ""


def last_name(node: ast.expr) -> str | None:
    """Return the final segment of a name or attribute chain, e.g. ``Final``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None
