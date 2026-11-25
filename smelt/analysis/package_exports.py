"""Conservative, direct public re-exports from analyzed package initializers."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.function_usage import definition_bindings, explicit_exports
from smelt.analysis.parsing import resolve_relative
from smelt.model import is_within

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from smelt.analysis.syntax import ModuleSyntax, SyntaxIndex


def package_facades(syntax: SyntaxIndex) -> dict[str, dict[str, str]]:
    """Direct re-exports of every analyzed package."""
    facades: dict[str, dict[str, str]] = {}
    for source in syntax.files.sources.values():
        if not source.is_package:
            continue
        module = syntax.for_path(source.path)
        facades[source.module] = {} if module is None else _direct_exports(module)
    return facades


def covering_package(
    facades: Mapping[str, Mapping[str, str]],
    base: str,
    *,
    caller: str,
    names: Collection[str],
    from_test: bool = False,
) -> str | None:
    """The nearest ancestor of ``base`` whose facade re-exports all ``names``.

    Production callers inside a package never route through its own initializer.
    """
    package = base.rpartition(".")[0]
    while package:
        exports = facades.get(package, {})
        if (from_test or not is_within(caller, package)) and all(
            exports.get(name) == f"{base}.{name}" for name in names
        ):
            return package
        package = package.rpartition(".")[0]
    return None


def _direct_exports(module: ModuleSyntax) -> dict[str, str]:
    """Public name -> imported name, excluding conditional or rebound bindings."""
    if not module.is_package:
        return {}
    exports = explicit_exports(module.tree)
    if exports is None:
        return {}
    declares_all = any(
        isinstance(node, ast.Name) and node.id == "__all__" for node in module.nodes
    )
    definitions = definition_bindings(module.tree)
    if "__getattr__" in definitions:
        return {}
    imports: dict[str, list[ast.AST]] = {}
    for node in module.nodes:
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return {}
                name = alias.asname or (
                    alias.name.split(".")[0]
                    if isinstance(node, ast.Import)
                    else alias.name
                )
                imports.setdefault(name, []).append(node)
    result: dict[str, str] = {}
    for node in module.tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        base = resolve_relative(
            module.module, is_package=True, level=node.level, target=node.module
        )
        for alias in node.names:
            name = alias.asname or alias.name
            if (
                name.startswith("_")
                or name in definitions
                or len(imports[name]) != 1
                or (declares_all and name not in exports)
            ):
                continue
            result[name] = f"{base}.{alias.name}"
    return result
