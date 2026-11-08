"""Recognize conventional class discriminator fields without executing code."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.typing_bindings import typing_bindings, typing_name

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_DISCRIMINATORS = frozenset({"type", "kind", "tag"})
_WRAPPERS = frozenset({"Annotated", "Required", "NotRequired"})


@dataclass(frozen=True)
class StringDiscriminator:
    node: ast.AnnAssign
    owner: str
    field: str
    value: str


def string_discriminators(tree: ast.Module) -> Iterator[StringDiscriminator]:
    bindings = typing_bindings(tree)
    for owner in ast.walk(tree):
        if not isinstance(owner, ast.ClassDef):
            continue
        for node in owner.body:
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id in _DISCRIMINATORS
                and (value := _literal_string(node.annotation, bindings)) is not None
            ):
                yield StringDiscriminator(node, owner.name, node.target.id, value)


def _literal_string(node: ast.expr, bindings: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None
    while isinstance(node, ast.Subscript):
        name = typing_name(node.value, bindings)
        parts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if name in _WRAPPERS and parts:
            node = parts[0]
            continue
        if (
            name == "Literal"
            and len(parts) == 1
            and isinstance(parts[0], ast.Constant)
            and isinstance(parts[0].value, str)
        ):
            return parts[0].value
        break
    return None
