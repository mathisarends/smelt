"""Recognize conventional class discriminator fields without executing code."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_DISCRIMINATORS = frozenset({"type", "kind", "tag"})
_TYPING_MODULES = frozenset({"typing", "typing_extensions"})
_WRAPPERS = frozenset({"Annotated", "Required", "NotRequired"})


@dataclass(frozen=True)
class StringDiscriminator:
    node: ast.AnnAssign
    owner: str
    field: str
    value: str


def string_discriminators(tree: ast.Module) -> Iterator[StringDiscriminator]:
    bindings = _typing_bindings(tree)
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


def _typing_bindings(tree: ast.Module) -> dict[str, str]:
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


def _typing_name(node: ast.expr, bindings: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        qualified = bindings.get(node.id, "")
    elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        qualified = f"{bindings.get(node.value.id, '')}.{node.attr}"
    else:
        return None
    module, _, name = qualified.rpartition(".")
    return name if module in _TYPING_MODULES else None


def _literal_string(node: ast.expr, bindings: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None
    while isinstance(node, ast.Subscript):
        name = _typing_name(node.value, bindings)
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
