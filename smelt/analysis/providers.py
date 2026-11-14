"""Recognize bounded Dishka constructor-forwarding review candidates."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


@dataclass(frozen=True)
class ProviderForwarding:
    node: ast.FunctionDef
    owner: str
    constructor: str
    dependencies: tuple[str, ...]
    registration: str


def provider_forwardings(tree: ast.Module) -> Iterator[ProviderForwarding]:
    bindings = _dishka_bindings(tree)
    for owner in tree.body:
        if (
            not isinstance(owner, ast.ClassDef)
            or owner.decorator_list
            or len(owner.bases) != 1
            or _qualified(owner.bases[0], bindings) != "dishka.Provider"
        ):
            continue
        for node in owner.body:
            if (
                not isinstance(node, ast.FunctionDef)
                or len(node.decorator_list) != 1
                or len(node.body) != 1
                or not isinstance(node.body[0], ast.Return)
                or not isinstance(node.body[0].value, ast.Call)
            ):
                continue
            decorator = node.decorator_list[0]
            options = decorator.keywords if isinstance(decorator, ast.Call) else []
            marker = decorator.func if isinstance(decorator, ast.Call) else decorator
            if _qualified(marker, bindings) != "dishka.provide" or (
                isinstance(decorator, ast.Call) and decorator.args
            ):
                continue
            if any(
                option.arg not in {"scope", "cache", "provides", "override"}
                for option in options
            ):
                continue
            candidate = _forwarding(
                owner.name, node, node.body[0].value, options=options
            )
            if candidate is not None:
                yield candidate


def _forwarding(
    owner: str, node: ast.FunctionDef, call: ast.Call, *, options: list[ast.keyword]
) -> ProviderForwarding | None:
    dependencies = _dependencies(node)
    if (
        dependencies is None
        or not isinstance(call.func, ast.Name | ast.Attribute)
        or node.returns is None
    ):
        return None
    constructor = ast.unparse(call.func)
    if not constructor.rsplit(".", 1)[-1][:1].isupper() or not _forwards_all(
        call, dependencies
    ):
        return None
    arguments = [constructor, *(ast.unparse(option) for option in options)]
    if not any(option.arg == "provides" for option in options):
        arguments.append(f"provides={ast.unparse(node.returns)}")
    return ProviderForwarding(
        node,
        owner,
        constructor,
        tuple(dependencies),
        "provide(" + ", ".join(arguments) + ")",
    )


def _dependencies(node: ast.FunctionDef) -> list[str] | None:
    signature = node.args
    if (
        signature.posonlyargs
        or signature.vararg
        or signature.kwarg
        or signature.defaults
        or any(default is not None for default in signature.kw_defaults)
        or not signature.args
        or signature.args[0].arg != "self"
    ):
        return None
    parameters = [*signature.args[1:], *signature.kwonlyargs]
    if any(parameter.annotation is None for parameter in parameters) or not isinstance(
        node.returns, ast.Name | ast.Attribute
    ):
        return None
    return [parameter.arg for parameter in parameters]


def _forwards_all(call: ast.Call, dependencies: list[str]) -> bool:
    if any(not isinstance(arg, ast.Name) for arg in call.args):
        return False
    positional = [arg.id for arg in call.args if isinstance(arg, ast.Name)]
    if positional != dependencies[: len(positional)]:
        return False
    if any(
        not isinstance(kw.value, ast.Name) or kw.arg != kw.value.id
        for kw in call.keywords
    ):
        return False
    forwarded = [*positional, *(kw.arg for kw in call.keywords)]
    return len(forwarded) == len(dependencies) and set(forwarded) == set(dependencies)


def _qualified(node: ast.expr, bindings: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        root = bindings.get(node.value.id)
        return f"{root}.{node.attr}" if root else None
    return None


def _dishka_bindings(tree: ast.Module) -> dict[str, str]:
    bound = _binding_counts(tree)
    result: dict[str, str] = {}
    if bound["*"]:
        return result
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == "dishka" and bound[local] == 1:
                    result[local] = "dishka"
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module == "dishka"
            and node.level == 0
        ):
            for alias in node.names:
                local = alias.asname or alias.name
                if bound[local] == 1:
                    result[local] = f"dishka.{alias.name}"
    return result


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
