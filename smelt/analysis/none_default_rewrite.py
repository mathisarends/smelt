"""Render an explicitly approved, behavior-changing None-default rewrite."""

from __future__ import annotations

import ast
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from smelt.analysis.parsing import AnalysisError

if TYPE_CHECKING:
    from smelt.analysis.none_defaults import NoneDefault


@dataclass(frozen=True)
class _Edit:
    start: int
    end: int
    fallback_start: int
    fallback_end: int
    parameter: str


def rewrite_none_defaults(source: str, candidates: list[NoneDefault]) -> str:
    data = source.encode("utf-8")
    offsets = [0]
    for line in data.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    edits: list[_Edit] = []
    keys: set[tuple[int, int]] = set()
    for candidate in candidates:
        node = candidate.node
        fallback = node.values[1]
        if node.end_lineno is None or fallback.end_lineno is None:
            raise AnalysisError("expression has no complete source range")
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + (node.end_col_offset or 0)
        if b"#" in data[start:end] or b"\\" in data[start:end]:
            raise AnalysisError(
                "comments or backslashes inside the expression require manual editing"
            )
        edits.append(
            _Edit(
                start,
                end,
                offsets[fallback.lineno - 1] + fallback.col_offset,
                offsets[fallback.end_lineno - 1] + (fallback.end_col_offset or 0),
                candidate.parameter,
            )
        )
        keys.add((node.lineno, node.col_offset))
    rewritten = _render(data, 0, len(data), edits).decode("utf-8")
    expected = _Expected(keys).visit(ast.parse(source))
    parsed = ast.parse(rewritten)
    if ast.dump(parsed) != ast.dump(expected):
        raise AnalysisError("rewrite changed more than the approved None defaults")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        compile(rewritten, "<smelt-none-default-fix>", "exec", dont_inherit=True)
    return rewritten


def _render(data: bytes, start: int, end: int, edits: list[_Edit]) -> bytes:
    inside = [edit for edit in edits if start <= edit.start and edit.end <= end]
    roots = [
        edit
        for edit in inside
        if not any(
            other is not edit and other.start <= edit.start and edit.end <= other.end
            for other in inside
        )
    ]
    pieces: list[bytes] = []
    cursor = start
    for edit in sorted(roots, key=lambda item: item.start):
        pieces.append(data[cursor : edit.start])
        parameter = edit.parameter.encode("utf-8")
        fallback = _render(data, edit.fallback_start, edit.fallback_end, edits)
        pieces.append(
            b"("
            + parameter
            + b" if "
            + parameter
            + b" is not None else ("
            + fallback
            + b"))"
        )
        cursor = edit.end
    pieces.append(data[cursor:end])
    return b"".join(pieces)


class _Expected(ast.NodeTransformer):
    def __init__(self, keys: set[tuple[int, int]]) -> None:
        self._keys = keys

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.expr:
        node = cast("ast.BoolOp", self.generic_visit(node))
        if (node.lineno, node.col_offset) not in self._keys:
            return node
        return ast.IfExp(
            test=ast.Compare(
                left=node.values[0],
                ops=[ast.IsNot()],
                comparators=[ast.Constant(value=None)],
            ),
            body=node.values[0],
            orelse=node.values[1],
        )
