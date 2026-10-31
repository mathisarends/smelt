"""Literal shape evidence without loading the test analyzer."""

import ast


def nonempty_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return bool(node.elts) and not any(
            isinstance(item, ast.Starred) for item in node.elts
        )
    if isinstance(node, ast.Dict):
        return bool(node.keys) and None not in node.keys
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str | bytes)
        and bool(node.value)
    )
