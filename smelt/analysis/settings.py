"""Bounded recognition of secret-named fields in known settings classes."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_SECRET_NAMES = (
    "password",
    "passwd",
    "secret",
    "api_key",
    "private_key",
    "access_token",
    "refresh_token",
    "token",
)
_SETTINGS = frozenset(
    {
        "pydantic_settings.BaseSettings",
        "pydantic.BaseSettings",
        "pydantic.v1.BaseSettings",
    }
)
_TYPING = frozenset(
    {
        "typing.Optional",
        "typing.Union",
        "typing.Annotated",
        "typing_extensions.Annotated",
    }
)


@dataclass(frozen=True)
class SecretSetting:
    node: ast.AnnAssign
    owner: str
    name: str
    plain_types: tuple[str, ...]


def secret_settings(tree: ast.Module) -> Iterator[SecretSetting]:
    bound = _binding_counts(tree)
    bindings = _imports(tree, bound)
    classes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and bound[node.name] == 1
        and not node.decorator_list
    }
    settings: set[str] = set()
    for _ in range(len(classes)):
        found = {
            name
            for name, node in classes.items()
            if any(
                _qualified(base, bindings) in _SETTINGS
                or (isinstance(base, ast.Name) and base.id in settings)
                for base in node.bases
            )
        }
        if found <= settings:
            break
        settings.update(found)
    for name in sorted(settings):
        for node in classes[name].body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(
                node.target, ast.Name
            ):
                continue
            field = node.target.id.lower()
            if not any(
                field == secret or field.endswith("_" + secret)
                for secret in _SECRET_NAMES
            ):
                continue
            plain = _plain_types(node.annotation, bindings, bound)
            if plain:
                yield SecretSetting(node, name, node.target.id, tuple(sorted(plain)))


def _binding_counts(tree: ast.Module) -> Counter[str]:
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
            isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar)
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


def _imports(tree: ast.Module, bound: Counter[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    if bound["*"]:
        return result
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                if bound[local] == 1:
                    result[local] = alias.name if alias.asname else local
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            for alias in node.names:
                local = alias.asname or alias.name
                if bound[local] == 1:
                    result[local] = f"{node.module}.{alias.name}"
    return result


def _plain_types(
    node: ast.expr, bindings: Mapping[str, str], bound: Counter[str]
) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _plain_types(
                ast.parse(node.value, mode="eval").body, bindings, bound
            )
        except SyntaxError:
            return set()
    if (
        isinstance(node, ast.Name)
        and node.id in {"str", "bytes"}
        and bound[node.id] == 0
    ):
        return {node.id}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _plain_types(node.left, bindings, bound) | _plain_types(
            node.right, bindings, bound
        )
    if (
        isinstance(node, ast.Subscript)
        and (wrapper := _qualified(node.value, bindings)) in _TYPING
    ):
        parts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if wrapper.endswith("Annotated"):
            parts = parts[:1]
        return set().union(*(_plain_types(part, bindings, bound) for part in parts))
    return set()


def _qualified(node: ast.expr, bindings: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.Attribute) and (parent := _qualified(node.value, bindings)):
        return f"{parent}.{node.attr}"
    return None
