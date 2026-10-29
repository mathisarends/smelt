"""Bounded evidence for module functions exposed only to analyzed tests."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.exports import resolve_dotted
from smelt.analysis.parsing import resolve_relative

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.syntax import ModuleSyntax, SyntaxIndex
    from smelt.analysis.test_syntax import TestSyntaxIndex

type Function = ast.FunctionDef | ast.AsyncFunctionDef
type Target = tuple[str, str | None]


@dataclass(frozen=True)
class TestOnlyPublicFunction:
    module: str
    path: str
    node: Function
    test_references: tuple[tuple[str, int], ...]


def test_only_public_functions(
    syntax: SyntaxIndex, tests: TestSyntaxIndex
) -> Iterator[TestOnlyPublicFunction]:
    test_references: dict[Target, set[tuple[str, int]]] = {}
    for test in tests.modules.values():
        for reference, qualified in [*test.imports, *test.attributes]:
            target = resolve_dotted(qualified, syntax.namespace)
            if target is not None:
                test_references.setdefault(target, set()).add(
                    (test.path, reference.lineno)
                )

    # Imports are evidence of production use even if the imported value is never
    # read. This also preserves deliberate facades and re-exports.
    production_uses: dict[Target, set[str]] = {}
    modules: list[ModuleSyntax] = []
    for source in sorted(syntax.files.sources.values(), key=lambda source: source.path):
        module = syntax.for_path(source.path)
        if module is None:
            continue
        modules.append(module)
        for target in module_references(module, syntax):
            production_uses.setdefault(target, set()).add(module.module)

    for module in modules:
        exports = explicit_exports(module.tree)
        module_parts = module.module.split(".")
        if any(
            production_uses.get((".".join(module_parts[:end]), None), set())
            - {module.module}
            for end in range(1, len(module_parts) + 1)
        ):
            continue
        definitions = definition_bindings(module.tree)
        for node in module.tree.body:
            if (
                not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                or node.name.startswith("_")
                or node.decorator_list
                or exports is None
                or node.name in exports
            ):
                continue
            target = (module.module, node.name)
            references = test_references.get(target)
            if not references or production_uses.get(target, set()) - {module.module}:
                continue
            # Rebinding or conditional replacement makes the exported object
            # uncertain; only an unambiguous top-level definition is a candidate.
            if definitions.get(node.name) != [node]:
                continue
            yield TestOnlyPublicFunction(
                module.module, module.path, node, tuple(sorted(references))
            )


def definition_bindings(tree: ast.Module) -> dict[str, list[ast.AST]]:
    definitions: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            definitions.setdefault(node.id, []).append(node)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            definitions.setdefault(node.name, []).append(node)
    return definitions


def module_references(module: ModuleSyntax, syntax: SyntaxIndex) -> Iterator[Target]:
    # Preserve every possible import binding. A same-named import in a different
    # local scope must not hide a real production consumer elsewhere in the file.
    bindings: dict[str, set[str]] = {}
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                bindings.setdefault(name, set()).add(
                    alias.name if alias.asname else name
                )
        elif isinstance(node, ast.ImportFrom):
            base = resolve_relative(
                module.module,
                is_package=module.is_package,
                level=node.level,
                target=node.module,
            )
            for alias in node.names:
                if alias.name == "*":
                    yield base, None
                    continue
                qualified = f"{base}.{alias.name}"
                bindings.setdefault(alias.asname or alias.name, set()).add(qualified)
                target = resolve_dotted(qualified, syntax.namespace)
                if target and target[1] is not None:
                    yield target
    parents = {
        child: parent
        for parent in ast.walk(module.tree)
        for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Name | ast.Attribute) and isinstance(
            node.ctx, ast.Load
        ):
            parent = parents.get(node)
            if isinstance(parent, ast.Attribute) and parent.value is node:
                continue
            for qualified in _qualified(node, bindings):
                if target := resolve_dotted(qualified, syntax.namespace):
                    # A module used as a value may escape via an alias, callback or
                    # reflection. Keep its API rather than guess which members flow.
                    yield target


def _qualified(node: ast.AST, bindings: dict[str, set[str]]) -> Iterator[str]:
    if isinstance(node, ast.Name):
        yield from sorted(bindings.get(node.id, ()))
    elif isinstance(node, ast.Attribute):
        for base in _qualified(node.value, bindings):
            yield f"{base}.{node.attr}"


def explicit_exports(tree: ast.Module) -> frozenset[str] | None:
    """None means a dynamic export declaration whose intent we cannot narrow."""
    exports: set[str] = set()
    handled: set[ast.AST] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign | ast.AugAssign):
            continue
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            targets = [node.target]
        names = [
            target
            for target in targets
            if isinstance(target, ast.Name) and target.id == "__all__"
        ]
        if not names:
            continue
        value = node.value
        if not isinstance(value, ast.List | ast.Tuple | ast.Set) or not all(
            isinstance(item, ast.Constant) and isinstance(item.value, str)
            for item in value.elts
        ):
            return None
        if isinstance(node, ast.AugAssign):
            if not isinstance(node.op, ast.Add):
                return None
        else:
            exports.clear()
        exports.update(
            item.value
            for item in value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        )
        handled.update(names)
    if any(
        isinstance(node, ast.Name) and node.id == "__all__" and node not in handled
        for node in ast.walk(tree)
    ):
        return None
    return frozenset(exports)
