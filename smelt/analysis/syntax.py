from __future__ import annotations

import ast
from bisect import bisect_left
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, NamedTuple

from pydantic import TypeAdapter

from smelt.analysis.exports import Namespace, namespace_of
from smelt.analysis.parsing import (
    AstCache,
    child_nodes,
    resolve_relative,
    tree_nodes,
    type_checking_lines,
)

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
        for node in tree_nodes(tree):
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


class ModuleIdentity(NamedTuple):
    module: str
    is_package: bool


class ImportName(NamedTuple):
    name: str
    asname: str | None
    lineno: int
    col_offset: int
    end_lineno: int | None
    end_col_offset: int | None

    @property
    def spelling(self) -> str:
        """The alias as written, like ``ast.unparse`` of the original alias."""
        return self.name if self.asname is None else f"{self.name} as {self.asname}"


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportStatement:
    """One import statement of a module, independent of where the module lives."""

    from_import: bool
    level: int
    # the ``from`` target; ``None`` for plain imports and ``from . import x``
    module: str | None
    names: tuple[ImportName, ...]
    lineno: int
    col_offset: int
    end_lineno: int | None
    end_col_offset: int | None
    # inside an ``if TYPE_CHECKING:`` block
    guarded: bool
    # outside every function and class body
    module_scope: bool

    @classmethod
    def of(
        cls,
        node: ast.Import | ast.ImportFrom,
        *,
        guarded: frozenset[int],
        module_scope: bool,
    ) -> ImportStatement:
        from_import = isinstance(node, ast.ImportFrom)
        return cls(
            from_import=from_import,
            level=node.level if isinstance(node, ast.ImportFrom) else 0,
            module=node.module if isinstance(node, ast.ImportFrom) else None,
            names=tuple(
                ImportName(
                    alias.name,
                    alias.asname,
                    alias.lineno,
                    alias.col_offset,
                    alias.end_lineno,
                    alias.end_col_offset,
                )
                for alias in node.names
            ),
            lineno=node.lineno,
            col_offset=node.col_offset,
            end_lineno=node.end_lineno,
            end_col_offset=node.end_col_offset,
            guarded=node.lineno in guarded,
            module_scope=module_scope,
        )


_IMPORT_STATEMENTS = TypeAdapter(tuple[ImportStatement, ...])


class ClassDefinition(NamedTuple):
    name: str
    line: int
    column: int


_CLASSES = TypeAdapter(tuple[ClassDefinition, ...])


@dataclass
class ModuleSyntax:
    module: str
    path: str
    tree: ast.Module
    is_package: bool
    # local name -> qualified name it was imported as
    bindings: dict[str, str] = field(default_factory=dict)

    @cached_property
    def nodes(self) -> tuple[ast.AST, ...]:
        """Breadth-first syntax inventory shared by readers of this immutable tree."""
        return tree_nodes(self.tree)

    @cached_property
    def imports(self) -> tuple[ast.Import | ast.ImportFrom, ...]:
        return tuple(
            node for node in self.nodes if isinstance(node, ast.Import | ast.ImportFrom)
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
        self._imports: dict[str, tuple[ImportStatement, ...]] = {}
        self._classes: dict[str, tuple[ClassDefinition, ...]] = {}

    def for_path(self, path: str) -> ModuleSyntax | None:
        cached = self._by_path.get(path)
        if cached is not None:
            return cached
        identity = self.module_identity(path)
        if identity is None:
            return None
        syntax = ModuleSyntax(
            identity.module, path, self._asts.parse(path), identity.is_package
        )
        _collect_bindings(syntax)
        self._by_path[path] = syntax
        return syntax

    def module_identity(self, path: str) -> ModuleIdentity | None:
        """The module an analyzed source or test path defines, without parsing it."""
        source = self.files.source_for_path(path)
        if source is not None:
            return ModuleIdentity(source.module, source.is_package)
        if path in self.files.tests:
            name = path.removesuffix(".py").replace("/", ".")
            return ModuleIdentity(name, name.endswith(".__init__"))
        return None

    def import_statements(self, path: str) -> tuple[ImportStatement, ...]:
        """Every import statement in ``ast.walk`` order, cached per file content."""
        if path not in self._imports:

            def build() -> tuple[ImportStatement, ...]:
                tree = self._asts.parse(path)
                guarded = type_checking_lines(tree)
                scoped = _module_scope_imports(tree)
                return tuple(
                    ImportStatement.of(
                        node, guarded=guarded, module_scope=node in scoped
                    )
                    for node in tree_nodes(tree)
                    if isinstance(node, ast.Import | ast.ImportFrom)
                )

            self._imports[path] = (
                build()
                if self.cache is None
                else self.cache.get(
                    "import-statements",
                    self.files.content_id(path),
                    _IMPORT_STATEMENTS,
                    build=build,
                )
            )
        return self._imports[path]

    def class_definitions(self, path: str) -> tuple[ClassDefinition, ...]:
        """Top-level classes in source order, cached per file content."""
        if path not in self._classes:

            def build() -> tuple[ClassDefinition, ...]:
                return tuple(
                    ClassDefinition(node.name, node.lineno, node.col_offset + 1)
                    for node in self._asts.parse(path).body
                    if isinstance(node, ast.ClassDef)
                )

            self._classes[path] = (
                build()
                if self.cache is None
                else self.cache.get(
                    "class-definitions",
                    self.files.content_id(path),
                    _CLASSES,
                    build=build,
                )
            )
        return self._classes[path]

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


def _module_scope_imports(tree: ast.Module) -> set[ast.AST]:
    found: set[ast.AST] = set()
    pending: list[ast.AST] = [tree]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Import | ast.ImportFrom):
            found.add(node)
        elif not isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            pending.extend(child_nodes(node))
    return found
