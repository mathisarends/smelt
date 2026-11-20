from __future__ import annotations

import ast
from bisect import bisect_left
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from smelt.analysis.exports import Namespace, namespace_of
from smelt.analysis.parsing import AstCache, resolve_relative, type_checking_lines

if TYPE_CHECKING:
    from smelt.analysis.cache import FactCache
    from smelt.analysis.files import FileIndex, SourceFile

_NAMESPACE = TypeAdapter(Namespace)


@dataclass(frozen=True, slots=True)
class AttributeCandidates:
    """Line ranges can rule out a scope before resolving cross-module types."""

    private: tuple[int, ...]
    navigation: tuple[int, ...]

    def may_access_private(self, statement: ast.stmt) -> bool:
        return self._contains(self.private, statement)

    def may_navigate(self, statement: ast.stmt) -> bool:
        return self._contains(self.navigation, statement)

    @classmethod
    def from_tree(cls, tree: ast.Module) -> AttributeCandidates:
        private: set[int] = set()
        navigation: set[int] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr.startswith("_")
                and not (node.attr.startswith("__") and node.attr.endswith("__"))
            ):
                private.add(node.lineno)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Attribute)
            ):
                navigation.add(node.lineno)
        return cls(tuple(sorted(private)), tuple(sorted(navigation)))

    @staticmethod
    def _contains(lines: tuple[int, ...], statement: ast.stmt) -> bool:
        index = bisect_left(lines, statement.lineno)
        return index < len(lines) and lines[index] <= (
            statement.end_lineno or statement.lineno
        )


_ATTRIBUTES = TypeAdapter(AttributeCandidates)


@dataclass
class ModuleSyntax:
    module: str
    path: str
    tree: ast.Module
    is_package: bool
    # local name -> qualified name it was imported as
    bindings: dict[str, str] = field(default_factory=dict)

    @cached_property
    def imports(self) -> tuple[ast.Import | ast.ImportFrom, ...]:
        return tuple(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
        )

    @cached_property
    def guarded_lines(self) -> frozenset[int]:
        return type_checking_lines(self.tree)


class SyntaxIndex:
    def __init__(
        self, files: FileIndex, asts: AstCache, *, cache: FactCache | None = None
    ) -> None:
        self.files = files
        self._asts = asts
        self.cache = cache
        self._by_path: dict[str, ModuleSyntax] = {}
        self._attributes: dict[str, AttributeCandidates] = {}
        self._namespaces: dict[str, Namespace | None] = {}

    def for_path(self, path: str) -> ModuleSyntax | None:
        cached = self._by_path.get(path)
        if cached is not None:
            return cached
        source = self.files.source_for_path(path)
        if source is not None:
            name, is_package = source.module, source.is_package
        elif path in self.files.tests:
            name = path.removesuffix(".py").replace("/", ".")
            is_package = name.endswith(".__init__")
        else:
            return None
        syntax = ModuleSyntax(name, path, self._asts.parse(path), is_package)
        _collect_bindings(syntax)
        self._by_path[path] = syntax
        return syntax

    def attribute_candidates(self, path: str) -> AttributeCandidates:
        if path not in self._attributes:

            def build() -> AttributeCandidates:
                return AttributeCandidates.from_tree(self._asts.parse(path))

            self._attributes[path] = (
                build()
                if self.cache is None
                else self.cache.get(
                    "attribute-candidates",
                    self.files.content_id(path),
                    _ATTRIBUTES,
                    build=build,
                )
            )
        return self._attributes[path]

    def namespace(self, module: str) -> Namespace | None:
        """Module bindings for ``analysis.exports``; namespaces have no initializer bindings."""
        if module not in self._namespaces:
            source = self.files.sources.get(module)
            self._namespaces[module] = (
                (Namespace(module) if module in self.files.packages else None)
                if source is None
                else self._namespace(source)
            )
        return self._namespaces[module]

    def _namespace(self, source: SourceFile) -> Namespace:
        def build() -> Namespace:
            return namespace_of(
                self._asts.parse(source.path),
                source.module,
                is_package=source.is_package,
            )

        if self.cache is None:
            return build()
        return self.cache.get(
            "namespace",
            [
                source.module,
                source.path,
                source.is_package,
                self.files.content_id(source.path),
            ],
            _NAMESPACE,
            build=build,
        )


def _collect_bindings(syntax: ModuleSyntax) -> None:
    for node in syntax.imports:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    syntax.bindings[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    syntax.bindings.setdefault(top, top)
        elif isinstance(node, ast.ImportFrom):
            base = resolve_relative(
                syntax.module,
                is_package=syntax.is_package,
                level=node.level,
                target=node.module,
            )
            for alias in node.names:
                if alias.name != "*":
                    qualified = f"{base}.{alias.name}" if base else alias.name
                    syntax.bindings[alias.asname or alias.name] = qualified
