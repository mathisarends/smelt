"""Narrow candidates for independent literal test cases, never lifecycle replay."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smelt.analysis.test_syntax import TestFunction

_MAX_CASES = 12
_MIN_CASES = 2


def independent_case_loops(test: TestFunction) -> list[ast.For]:
    candidates: list[ast.For] = []
    for node in test.node.body:
        if (
            not isinstance(node, ast.For)
            or node.orelse
            or not isinstance(node.iter, ast.List | ast.Tuple)
        ):
            continue
        if not _MIN_CASES <= len(node.iter.elts) <= _MAX_CASES or not all(
            _literal(value) for value in node.iter.elts
        ):
            continue
        if _independent_body(node):
            candidates.append(node)
    return candidates


def _literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    return isinstance(node, ast.List | ast.Tuple | ast.Dict) and all(
        _literal(child)
        for child in ast.iter_child_nodes(node)
        if not isinstance(child, ast.expr_context)
    )


def _independent_body(loop: ast.For) -> bool:
    assigned = {node.id for node in ast.walk(loop.target) if isinstance(node, ast.Name)}
    locals_in_loop = {
        node.id
        for statement in loop.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    action = assertion = False
    for statement in loop.body:
        if not isinstance(statement, ast.Assign | ast.AnnAssign | ast.Assert):
            return False
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
            if isinstance(statement, ast.AnnAssign)
            else []
        )
        if any(not isinstance(target, ast.Name) for target in targets):
            return False
        value = statement.test if isinstance(statement, ast.Assert) else statement.value
        if value is None:
            return False
        for child in ast.walk(value):
            if (
                isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Load)
                and child.id in locals_in_loop
                and child.id not in assigned
            ):
                return False
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                # A method on pre-existing state can participate in replay/handoff.
                receiver = child.func.value
                if not isinstance(receiver, ast.Name) or receiver.id not in assigned:
                    return False
        assertion |= isinstance(statement, ast.Assert)
        action |= any(isinstance(child, ast.Call) for child in ast.walk(value))
        assigned.update(target.id for target in targets if isinstance(target, ast.Name))
    return action and assertion
