from __future__ import annotations

import ast

from smelt.analysis.errors import (
    AnalysisError as AnalysisError,  # noqa: PLC0414 - compatibility re-export
)


def tree_nodes(tree: ast.AST) -> tuple[ast.AST, ...]:
    """Every node below ``tree`` in ``ast.walk`` order, walked once per root node.

    Analyses never mutate parsed trees, so every reader of one module, class or
    function shares a walk. Use this instead of ``ast.walk``: two dozen rules
    walk each module. Only code that rewrites the tree it walks needs ``ast.walk``.
    """
    # The tuple includes the root. Store it on the tree so the cycle can be
    # collected once the analysis drops the tree.
    nodes: tuple[ast.AST, ...] | None = tree.__dict__.get("_smelt_walk")
    if nodes is None:
        ordered = [tree]
        # The list is its own breadth-first queue, which is the order of ast.walk.
        for node in ordered:
            ordered.extend(_child_nodes(node))
        nodes = tuple(ordered)
        tree.__dict__["_smelt_walk"] = nodes
    return nodes


def resolve_relative(
    module: str, *, is_package: bool, level: int, target: str | None
) -> str:
    """Resolve ``from <level dots><target> import ...`` inside ``module``."""
    if level == 0:
        return target or ""
    parts = module.split(".")
    if not is_package:
        parts = parts[:-1]
    if level > 1:
        parts = parts[: len(parts) - (level - 1)]
    base = ".".join(parts)
    if target:
        return f"{base}.{target}" if base else target
    return base


def _child_nodes(node: ast.AST) -> list[ast.AST]:
    """The children ``ast.iter_child_nodes`` yields, in its order, without its calls."""
    children: list[ast.AST] = []
    fields = node.__dict__
    for name in node._fields:
        value = fields.get(name)
        if isinstance(value, list):
            children += [item for item in value if isinstance(item, ast.AST)]
        elif isinstance(value, ast.AST):
            children.append(value)
    return children
