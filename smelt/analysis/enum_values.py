"""Bounded recognition of direct member accesses on standard StrEnum classes."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.exports import Namespace, resolve_dotted, resolve_export
from smelt.analysis.parsing import resolve_relative

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.syntax import ModuleSyntax, SyntaxIndex


@dataclass(frozen=True)
class StrEnumValueAccess:
    module: str
    path: str
    node: ast.Attribute
    enum: str
    member: str


def str_enum_value_accesses(syntax: SyntaxIndex) -> Iterator[StrEnumValueAccess]:
    enums = _Enums(syntax)
    for module in enums.modules.values():
        namespace = enums.namespaces[module.module]
        for node in ast.walk(module.tree):
            if not (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and node.attr == "value"
                and isinstance(node.value, ast.Attribute)
            ):
                continue
            enum = enums.resolve(node.value.value, namespace)
            if enum is not None and node.value.attr in enums.members.get(enum, ()):
                yield StrEnumValueAccess(
                    module.module, module.path, node, enum, node.value.attr
                )


def _bound_names(node: ast.AST) -> tuple[str, ...]:
    match node:
        case (
            ast.Name(id=name, ctx=ast.Store() | ast.Del())
            | ast.arg(arg=name)
            | ast.FunctionDef(name=name)
            | ast.AsyncFunctionDef(name=name)
            | ast.ClassDef(name=name)
            | ast.TypeVar(name=name)
            | ast.ParamSpec(name=name)
            | ast.TypeVarTuple(name=name)
        ):
            return (name,)
        case ast.Import() | ast.ImportFrom():
            return tuple(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        case (
            ast.ExceptHandler(name=name)
            | ast.MatchAs(name=name)
            | ast.MatchStar(name=name)
            | ast.MatchMapping(rest=name)
        ):
            return (name,) if name else ()
        case ast.Global(names=names) | ast.Nonlocal(names=names):
            return tuple(names)
    return ()


def _binding_counts(tree: ast.Module) -> Counter[str]:
    bound = Counter(name for node in ast.walk(tree) for name in _bound_names(node))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute | ast.Subscript) and isinstance(
            node.ctx, ast.Store | ast.Del
        ):
            root = node.value
            while isinstance(root, ast.Attribute | ast.Subscript):
                root = root.value
            if isinstance(root, ast.Name):
                bound[root.id] += 1
    return bound


def _namespace(module: ModuleSyntax) -> Namespace:
    # A binding shadowed even in another scope is unknown. This intentionally
    # trades some recognition for avoiding guesses about Python name resolution.
    bound = _binding_counts(module.tree)
    imported: dict[str, str] = {}
    if not bound["*"]:
        for node in module.tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    if bound[name] == 1:
                        imported[name] = alias.name if alias.asname else name
            elif isinstance(node, ast.ImportFrom):
                base = resolve_relative(
                    module.module,
                    is_package=module.is_package,
                    level=node.level,
                    target=node.module,
                )
                for alias in node.names:
                    name = alias.asname or alias.name
                    if bound[name] == 1 and name != "*":
                        imported[name] = f"{base}.{alias.name}"
    # Block fallback resolution to a submodule for uncertain/rebound names.
    return Namespace(module.module, imported, frozenset(bound.keys() - imported.keys()))


class _Enums:
    def __init__(self, syntax: SyntaxIndex) -> None:
        self.modules = {
            source.module: module
            for source in sorted(
                syntax.files.sources.values(), key=lambda item: item.path
            )
            if (module := syntax.for_path(source.path)) is not None
        }
        self.namespaces = {
            name: _namespace(module) for name, module in self.modules.items()
        }
        for package in syntax.files.packages:
            self.namespaces.setdefault(package, Namespace(package))
        self.namespaces.setdefault(
            "enum", Namespace("enum", defined=frozenset({"StrEnum", "auto"}))
        )
        self.members: dict[str, frozenset[str]] = {}
        for module in self.modules.values():
            bound = _binding_counts(module.tree)
            if bound["*"]:
                continue
            for node in module.tree.body:
                if isinstance(node, ast.ClassDef) and bound[node.name] == 1:
                    self.members[f"{module.module}.{node.name}"] = self._members(
                        node, self.namespaces[module.module]
                    )

    def resolve(self, node: ast.expr, namespace: Namespace) -> str | None:
        resolved = self._target(node, namespace)
        if resolved is None or resolved[1] is None:
            return None
        return f"{resolved[0]}.{resolved[1]}"

    def _target(
        self, node: ast.expr, namespace: Namespace
    ) -> tuple[str, str | None] | None:
        if isinstance(node, ast.Name):
            qualified = namespace.imported.get(node.id, f"{namespace.module}.{node.id}")
            return resolve_dotted(qualified, self.namespaces.get)
        if isinstance(node, ast.Attribute):
            base = self._target(node.value, namespace)
            # Attributes of an already resolved class/member are unknown, rather
            # than another spelling of that class.
            if base is not None and base[1] is None:
                return resolve_export(base[0], node.attr, self.namespaces.get)
        return None

    def _members(self, node: ast.ClassDef, namespace: Namespace) -> frozenset[str]:
        if (
            len(node.bases) != 1
            or self.resolve(node.bases[0], namespace) != "enum.StrEnum"
            or "enum" in self.modules
            or node.keywords
            or node.decorator_list
        ):
            return frozenset()
        if any(
            name
            in {
                "value",
                "_ignore_",
                "__new__",
                "__init__",
                "__getattribute__",
                "__getattr__",
            }
            for child in node.body
            for binding in (
                ast.walk(child)
                if isinstance(child, ast.Assign | ast.AnnAssign | ast.AugAssign)
                else (child,)
            )
            for name in _bound_names(binding)
        ):
            return frozenset()
        members: set[str] = set()
        value: ast.expr | None
        for child in node.body:
            if isinstance(child, ast.Assign):
                targets, value = child.targets, child.value
            elif isinstance(child, ast.AnnAssign):
                targets, value = [child.target], child.value
            else:
                continue
            if (
                (isinstance(value, ast.Constant) and isinstance(value.value, str))
                or (isinstance(value, ast.Name) and value.id in members)
                or (
                    isinstance(value, ast.Call)
                    and not value.args
                    and not value.keywords
                    and self.resolve(value.func, namespace) == "enum.auto"
                )
            ):
                members.update(
                    target.id
                    for target in targets
                    if isinstance(target, ast.Name) and not target.id.startswith("_")
                )
        return frozenset(members)
