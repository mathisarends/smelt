"""Call-only assertions on known async mocks, with bounded local evidence."""

from __future__ import annotations

import ast
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smelt.analysis.test_syntax import MockObservation, TestFunction


def call_only_async_mocks(test: TestFunction) -> list[MockObservation]:
    asserted = _asserted_nodes(test)
    parents = {
        child: parent
        for parent in ast.walk(test.node)
        for child in ast.iter_child_nodes(parent)
    }
    observations = [
        item
        for item in test.mock_observations
        if isinstance(item.node, ast.Call) or item.node in asserted
    ]
    awaited = {item.origin for item in observations if item.kind == "await"}
    risks: dict[str, MockObservation] = {}
    for item in observations:
        if item.kind == "call" and item.origin not in awaited:
            if isinstance(item.node, ast.Attribute) and _checks_no_call(
                item.node, parents
            ):
                continue
            risks.setdefault(item.origin, item)
    return list(risks.values())


def _asserted_nodes(test: TestFunction) -> set[ast.AST]:
    writes = Counter(
        node.id
        for node in ast.walk(test.node)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del)
    )
    assignments: dict[str, list[ast.expr]] = {}
    for node in ast.walk(test.node):
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store):
                        assignments.setdefault(name.id, []).append(node.value)
    # Follow only single-assignment local values, such as unpacked call_args.
    pending: list[ast.AST] = list(test.assertions)
    pending.extend(
        call.node
        for call in test.calls
        if call.qualified.startswith("unittest.TestCase.assert")
    )
    asserted: set[ast.AST] = set()
    followed: set[str] = set()
    while pending:
        expression = pending.pop()
        for node in ast.walk(expression):
            asserted.add(node)
            if isinstance(node, ast.Name) and node.id not in followed:
                followed.add(node.id)
                values = assignments.get(node.id, [])
                if (
                    len(values) == 1
                    and writes[node.id] == 1
                    and values[0].lineno < node.lineno
                ):
                    pending.append(values[0])
    return asserted


def _checks_no_call(node: ast.Attribute, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(node)
    if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not):
        return True
    if isinstance(parent, ast.Compare) and len(parent.ops) == 1:
        left, right = parent.left, parent.comparators[0]
        other = right if left is node else left if right is node else None
        return isinstance(parent.ops[0], ast.Eq | ast.Is) and (
            (isinstance(other, ast.Constant) and other.value in (None, False, 0))
            or (
                node.attr == "call_args_list"
                and isinstance(other, ast.List)
                and not other.elts
            )
        )
    return False
