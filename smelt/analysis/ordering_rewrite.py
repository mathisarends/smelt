"""Render approved statement moves while preserving source text and trivia."""

from __future__ import annotations

import ast
import copy
import io
import re
import tokenize
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.definition_blocks import statement_line
from smelt.analysis.parsing import AnalysisError

if TYPE_CHECKING:
    from smelt.analysis.definition_blocks import ScopeNode
    from smelt.analysis.ordering import FileOrder, OrderSection

_CODING = re.compile(r"coding[:=]\s*([-\w.]+)")
_FILE_PRAGMA = re.compile(
    r"\s*#\s*(?:ruff:|mypy:|pyright:|flake8:|type:\s*ignore|smelt:\s*ignore-file)"
)
_ENCODING_LINES = 2
_FIX_BOUNDARY = re.compile(
    r"(?m)^\s*#\s*(?:fmt:\s*(?:off|skip)\b|pylint:\s*(?:disable|enable)\b)"
)


@dataclass(frozen=True)
class Span:
    start: int
    end: int


@dataclass(frozen=True)
class Move:
    section: OrderSection
    parts: tuple[Span, ...]

    @property
    def span(self) -> Span:
        return Span(self.parts[0].start, self.parts[-1].end)


def rewrite_order(source: str, result: FileOrder, sections: list[OrderSection]) -> str:
    if not sections:
        return source
    if any(section.fix_reason for section in sections):
        raise AnalysisError("an ordering section requires manual review")
    if _FIX_BOUNDARY.search(source):
        raise AnalysisError(
            "formatter or suppression region prevents safe ordering fixes"
        )
    if any(
        "\t" in line[: len(line) - len(line.lstrip())] for line in source.splitlines()
    ):
        raise AnalysisError("tab indentation requires manual ordering review")
    if ast.dump(ast.parse(source), include_attributes=False) != ast.dump(
        result.tree, include_attributes=False
    ):
        raise AnalysisError("source changed since ordering analysis")
    _compile_source(source, result.path)
    newline = "\r\n" if "\r\n" in source else "\n"
    rewritten = _render(source, Span(0, len(source)), _moves(source, sections), newline)
    if not source.endswith(("\n", "\r")) and rewritten.endswith(newline):
        rewritten = rewritten[: -len(newline)]
    try:
        parsed = ast.parse(rewritten)
    except SyntaxError as exc:
        raise AnalysisError("ordering rewrite did not preserve valid source") from exc
    expected = _expected_tree(result.tree, sections)
    if ast.dump(parsed, include_attributes=False) != ast.dump(
        expected, include_attributes=False
    ):
        raise AnalysisError(
            "ordering rewrite changed more than the approved statement order"
        )
    _compile_source(rewritten, result.path)
    return rewritten


def _header_lines(source: str, owner: ScopeNode) -> int:
    if isinstance(owner, ast.Module):
        first = statement_line(owner.body[0]) if owner.body else 1
        fixed = 0
        for index, line in enumerate(source.splitlines()[: first - 1]):
            if (
                (index == 0 and line.startswith("#!"))
                or (index < _ENCODING_LINES and _CODING.search(line))
                or _FILE_PRAGMA.match(line)
            ):
                fixed = index + 1
        return fixed
    depth = 0
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.start < (owner.lineno, owner.col_offset):
            continue
        if token.type == tokenize.OP:
            if token.string in {"(", "[", "{"}:
                depth += 1
            elif token.string in {")", "]", "}"}:
                depth -= 1
            elif token.string == ":" and depth == 0:
                return token.end[0]
    raise AnalysisError("cannot identify the class suite for a safe ordering fix")


def _statement_end(node: ast.stmt, lines: list[str]) -> int:
    end = node.end_lineno or node.lineno
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return end
    for index in range(end, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        indentation = len(line) - len(line.lstrip(" \t"))
        if line.lstrip().startswith("#") and indentation > node.col_offset:
            end = index + 1
        else:
            break
    return end


def _moves(source: str, sections: list[OrderSection]) -> list[Move]:
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    scopes: dict[int, dict[int, Span]] = {}
    moves: list[Move] = []
    for section in sections:
        owner = section.owner
        if id(owner) not in scopes:
            cursor = _header_lines(source, owner)
            spans: dict[int, Span] = {}
            for node in owner.body:
                end = _statement_end(node, lines)
                spans[id(node)] = Span(offsets[cursor], offsets[end])
                cursor = end
            scopes[id(owner)] = spans
        spans = scopes[id(owner)]
        parts = tuple(
            Span(spans[id(block.nodes[0])].start, spans[id(block.nodes[-1])].end)
            for block in section.blocks
        )
        if any(part.start >= part.end for part in parts):
            raise AnalysisError("overlapping source lines cannot be fixed safely")
        moves.append(Move(section, parts))
    return moves


def _render(source: str, span: Span, moves: list[Move], newline: str) -> str:
    inside = [
        move
        for move in moves
        if span.start <= move.span.start and move.span.end <= span.end
    ]
    roots = [
        move
        for move in inside
        if not any(
            other is not move
            and other.span.start <= move.span.start
            and move.span.end <= other.span.end
            for other in inside
        )
    ]
    pieces: list[str] = []
    cursor = span.start
    for move in sorted(roots, key=lambda item: item.span.start):
        if move.span.start < cursor:
            raise AnalysisError("overlapping ordering edits cannot be fixed safely")
        pieces.append(source[cursor : move.span.start])
        children = [
            item
            for item in inside
            if item is not move
            and move.span.start <= item.span.start
            and item.span.end <= move.span.end
        ]
        blocks = [
            _render(source, move.parts[index], children, newline)
            for index in move.section.order
        ]
        for index, block in enumerate(blocks):
            if (
                index + 1 < len(blocks)
                and block
                and not block.endswith(("\n", "\r"))
                and not blocks[index + 1].startswith(("\n", "\r"))
            ):
                pieces.append(block + newline)
                continue
            pieces.append(block)
        cursor = move.span.end
    pieces.append(source[cursor : span.end])
    return "".join(pieces)


def _expected_tree(tree: ast.Module, sections: list[OrderSection]) -> ast.Module:
    expected = copy.deepcopy(tree)
    copies = {
        id(original): cloned
        for original, cloned in zip(ast.walk(tree), ast.walk(expected), strict=True)
    }
    for section in sections:
        owner = copies[id(section.owner)]
        if not isinstance(owner, ast.Module | ast.ClassDef):
            raise AnalysisError("invalid ordering scope")
        nodes = [
            copies[id(node)]
            for index in section.order
            for node in section.blocks[index].nodes
        ]
        replacement: list[ast.stmt] = []
        for node in nodes:
            if not isinstance(node, ast.stmt):
                raise AnalysisError("invalid statement in an ordering rewrite")
            replacement.append(node)
        owner.body[section.blocks[0].first : section.blocks[-1].stop] = replacement
    return expected


def _compile_source(source: str, path: str) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            compile(source, path, "exec", dont_inherit=True)
    except SyntaxError as exc:
        raise AnalysisError(f"source cannot be safely compiled: {exc.msg}") from exc
