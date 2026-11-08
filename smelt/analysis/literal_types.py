"""Content-local Literal evidence; grouping never requires pairwise file scans."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from smelt.analysis.typing_bindings import typing_bindings, typing_name

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from smelt.analysis.context import AnalysisContext

type LiteralMembers = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class LiteralType:
    members: LiteralMembers
    scope: str
    line: int
    column: int
    end_line: int
    end_column: int


_FACTS = TypeAdapter(tuple[LiteralType, ...])


def literal_types(ctx: AnalysisContext, path: str) -> tuple[LiteralType, ...]:
    def build() -> tuple[LiteralType, ...]:
        # Every supported direct import/access spells Literal in the source.
        # Negative results are facts too; do not parse them on warm checks.
        if "Literal" not in ctx.files.read_text(path):
            return ()
        tree = ctx.asts.parse(path)
        collector = _Collector(typing_bindings(tree))
        if collector.bindings:
            collector.visit(tree)
        return tuple(collector.found)

    if ctx.cache is None:
        return build()
    return ctx.cache.get(
        "literal-types",
        [path, ctx.files.content_id(path)],
        _FACTS,
        build,
    )


class _Collector(ast.NodeVisitor):
    def __init__(self, bindings: Mapping[str, str]) -> None:
        self.bindings = bindings
        self.found: list[LiteralType] = []
        self.scope = "module"

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        previous = self.scope
        self.scope = f"{previous}.{node.name}"
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if node.args.vararg:
            arguments.append(node.args.vararg)
        if node.args.kwarg:
            arguments.append(node.args.kwarg)
        for argument in arguments:
            if argument.annotation is not None:
                self._annotation(argument.annotation, f"parameter:{argument.arg}")
        if node.returns is not None:
            self._annotation(node.returns, "return")
        for statement in node.body:
            self.visit(statement)
        self.scope = previous

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        previous = self.scope
        self.scope = f"{previous}.{node.name}"
        for statement in node.body:
            self.visit(statement)
        self.scope = previous

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        target = ast.unparse(node.target)
        self._annotation(node.annotation, f"annotation:{target}")
        if (
            typing_name(node.annotation, self.bindings) == "TypeAlias"
            and node.value is not None
        ):
            self._annotation(node.value, f"alias:{target}")

    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        self._annotation(node.value, f"alias:{node.name.id}")

    def visit_Assign(self, node: ast.Assign) -> None:
        if (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Subscript)
            and typing_name(node.value.value, self.bindings) in {"Literal", "Annotated"}
        ):
            self._annotation(node.value, f"alias:{node.targets[0].id}")

    def _annotation(self, root: ast.expr, role: str) -> None:
        for node, members in _annotation_literals(root, self.bindings):
            self.found.append(
                LiteralType(
                    members,
                    f"{self.scope}:{role}",
                    node.lineno,
                    node.col_offset + 1,
                    node.end_lineno or node.lineno,
                    (node.end_col_offset or node.col_offset) + 1,
                )
            )


def _annotation_literals(
    node: ast.expr, bindings: Mapping[str, str]
) -> Iterator[tuple[ast.expr, LiteralMembers]]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            parsed = ast.parse(node.value, mode="eval").body
        except (SyntaxError, ValueError):
            return
        for _, quoted_members in _annotation_literals(parsed, bindings):
            # A quoted annotation's diagnostic points at its real source range.
            yield node, quoted_members
    elif isinstance(node, ast.Subscript):
        name = typing_name(node.value, bindings)
        if name == "Literal":
            members = _literal_members(node, bindings)
            if members is not None and len(members) >= 2:  # noqa: PLR2004 - a duplicate union must have multiple distinct values
                yield node, members
            return
        parts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if name == "Annotated":
            parts = parts[:1]
        for part in parts:
            yield from _annotation_literals(part, bindings)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        yield from _annotation_literals(node.left, bindings)
        yield from _annotation_literals(node.right, bindings)
    elif isinstance(node, ast.List | ast.Tuple):
        for part in node.elts:
            yield from _annotation_literals(part, bindings)


def _literal_members(
    node: ast.Subscript, bindings: Mapping[str, str]
) -> LiteralMembers | None:
    parts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
    members: set[tuple[str, str]] = set()
    for part in parts:
        if (
            isinstance(part, ast.Subscript)
            and typing_name(part.value, bindings) == "Literal"
        ):
            nested = _literal_members(part, bindings)
            if nested is None:
                return None
            members.update(nested)
        elif isinstance(part, ast.Constant) and type(part.value) in {
            str,
            int,
            bool,
            type(None),
        }:
            # Tags distinguish 0/False and 1/True. repr preserves string escapes.
            members.add((type(part.value).__name__, repr(part.value)))
        elif (
            isinstance(part, ast.UnaryOp)
            and isinstance(part.op, ast.USub | ast.UAdd)
            and isinstance(part.operand, ast.Constant)
            and type(part.operand.value) is int
        ):
            sign = -1 if isinstance(part.op, ast.USub) else 1
            members.add(("int", repr(sign * part.operand.value)))
        else:
            # Names, enum members and calls need semantic resolution, not eval.
            return None
    return tuple(sorted(members))
