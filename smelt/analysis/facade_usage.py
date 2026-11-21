"""Bounded symbol provenance and runtime demand for package facades."""

from __future__ import annotations

import ast
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, NamedTuple

from pydantic import TypeAdapter

from smelt.analysis.exports import MAX_HOPS
from smelt.analysis.function_usage import definition_bindings, explicit_exports
from smelt.analysis.lazy_exports import static_lazy_exports
from smelt.analysis.parsing import resolve_relative
from smelt.model import is_within

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import FileIndex
    from smelt.analysis.imports import ImportIndex
    from smelt.analysis.syntax import ModuleSyntax, SyntaxIndex


@dataclass(frozen=True, slots=True)
class _Symbol:
    module: str
    name: str


class _Location(NamedTuple):
    line: int
    column: int


class _CanonicalExportIssue(NamedTuple):
    symbol: str
    message: str


@dataclass(slots=True)
class _Importers:
    sources: set[str] = field(default_factory=set)
    tests: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _Definition:
    module: str
    name: str
    path: str
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class _ModuleFacts:
    """Facade inputs of one module, a pure function of its path and content."""

    module: str
    opaque: bool = False
    # name -> the location of its single top-level definition
    definitions: dict[str, _Location] = field(default_factory=dict)
    # name -> the qualified name it statically re-exports
    reexports: dict[str, str] = field(default_factory=dict)
    bound: frozenset[str] = frozenset()
    public: frozenset[str] | None = None
    declares_all: bool = False
    # runtime ``from base import names`` statements, without private names
    imports: tuple[tuple[str, tuple[str, ...]], ...] = ()


_FACTS = TypeAdapter(_ModuleFacts)


@dataclass(frozen=True, slots=True)
class _Use:
    definition: _Definition
    importer: str
    test: bool


@dataclass(frozen=True, slots=True)
class FacadeGap:
    module: str
    name: str
    path: str
    line: int
    column: int
    source_importers: tuple[str, ...]
    test_importers: tuple[str, ...]
    reexports: tuple[str, ...]
    cycle: tuple[str, ...]
    needs_import: bool


class FacadeUsage:
    """Facade demand shared by every rule and check of one analysis run."""

    def __init__(
        self, syntax: SyntaxIndex, imports: ImportIndex, *, files: FileIndex
    ) -> None:
        self._syntax = syntax
        self._files = files
        self._facts: dict[str, _ModuleFacts | None] = {}
        self._resolved: dict[_Symbol, _Definition | None] = {}
        # Uses collected with tests are a superset; queries filter test uses.
        self._uses: dict[bool, tuple[_Use, ...]] = {}
        self._exports = self._collect_exports()
        self._adjacency = imports.first_party_edges(include_type_checking=False)

    def canonical_exports(
        self, declarations: dict[str, str]
    ) -> tuple[dict[str, str], list[_CanonicalExportIssue]]:
        """Verify explicit decisions against identity and actual static exports."""
        verified: dict[str, str] = {}
        issues: list[_CanonicalExportIssue] = []
        owners: dict[_Definition, str] = {}
        for symbol, export in sorted(declarations.items()):
            module, _, name = symbol.rpartition(".")
            definition = self._resolve(module, name)
            owner, _, public_name = export.rpartition(".")
            target = self._resolve(owner, public_name)
            if definition is None:
                issues.append(
                    _CanonicalExportIssue(
                        symbol,
                        f"cannot statically resolve {symbol}; use an analyzed, unambiguous definition or static re-export",
                    )
                )
            elif target != definition or export not in self._exports.get(
                definition, ()
            ):
                issues.append(
                    _CanonicalExportIssue(
                        symbol,
                        f"{export} is not a verified public export of {symbol}; declare a static re-export and include it in __all__ when declared. Unsupported dynamic exports cannot verify ownership.",
                    )
                )
            elif definition in owners and owners[definition] != export:
                issues.append(
                    _CanonicalExportIssue(
                        symbol,
                        f"conflicting canonical exports for {definition.module}.{definition.name}: {owners[definition]} and {export}; choose one public owner",
                    )
                )
            else:
                owners[definition] = export
                verified[f"{definition.module}.{definition.name}"] = export
        return verified, issues

    def gaps(self, package: str, *, count_tests: bool) -> Iterator[FacadeGap]:
        source = self._files.sources.get(package)
        if source is None or not source.is_package:
            return
        facade = self._facts_for(source.path)
        if facade is None or facade.opaque or facade.public is None:
            return
        exports = facade.public
        declares_all = facade.declares_all
        callers: dict[_Definition, _Importers] = {}
        for use in self._uses_for(count_tests=count_tests):
            definition = use.definition
            if (
                (use.test and not count_tests)
                or definition.module == package
                or not is_within(definition.module, package)
                or (is_within(use.importer, package) and not use.test)
            ):
                continue
            groups = callers.setdefault(definition, _Importers())
            if use.test:
                groups.tests.add(use.importer)
            else:
                groups.sources.add(use.importer)
        for definition, importers in sorted(
            callers.items(), key=lambda item: (item[0].module, item[0].name)
        ):
            name = definition.name
            existing = self._resolve(package, name)
            if existing == definition and (not declares_all or name in exports):
                continue
            # A conflicting/uncertain binding must never suggest overwriting an API.
            if existing != definition and (name in facade.bound or name in exports):
                continue
            yield FacadeGap(
                definition.module,
                name,
                definition.path,
                definition.line,
                definition.column,
                tuple(sorted(importers.sources)),
                tuple(sorted(importers.tests)),
                tuple(
                    sorted(self._exports.get(definition, set()) - {f"{package}.{name}"})
                ),
                self._cycle(package, definition.module),
                existing != definition,
            )

    def _uses_for(self, *, count_tests: bool) -> tuple[_Use, ...]:
        if True in self._uses:
            return self._uses[True]
        if count_tests not in self._uses:
            self._uses[count_tests] = tuple(self._collect_uses(count_tests=count_tests))
        return self._uses[count_tests]

    def _collect_uses(self, *, count_tests: bool) -> Iterator[_Use]:
        files = self._files
        paths = [
            (source.path, files.is_test_path(source.path))
            for source in files.sources.values()
        ]
        if count_tests:
            paths.extend((path, True) for path in files.tests)
        for path, test in paths:
            if test and not count_tests:
                continue
            facts = self._facts_for(path)
            if facts is None:
                continue
            for base, names in facts.imports:
                source = files.sources.get(base)
                if source is None or source.is_package:
                    continue  # published facade imports do not represent deep coupling
                for name in names:
                    definition = self._resolve(base, name)
                    if definition is not None and not definition.name.startswith("_"):
                        yield _Use(definition, facts.module, test)

    def _collect_exports(self) -> dict[_Definition, set[str]]:
        result: dict[_Definition, set[str]] = defaultdict(set)
        for source in self._files.sources.values():
            facts = self._facts_for(source.path)
            if facts is None or facts.opaque:
                continue
            exports = facts.public
            declares_all = facts.declares_all
            if exports is None or (not source.is_package and not declares_all):
                continue
            for name in (*facts.definitions, *facts.reexports):
                if name.startswith("_") or (declares_all and name not in exports):
                    continue
                definition = self._resolve(source.module, name)
                if definition is not None:
                    result[definition].add(f"{source.module}.{name}")
        return result

    def _resolve(self, module: str, name: str) -> _Definition | None:
        key = _Symbol(module, name)
        if key not in self._resolved:
            self._resolved[key] = self._follow(module, name)
        return self._resolved[key]

    def _follow(self, module: str, name: str) -> _Definition | None:
        seen: set[_Symbol] = set()
        while len(seen) < MAX_HOPS and _Symbol(module, name) not in seen:
            seen.add(_Symbol(module, name))
            source = self._files.sources.get(module)
            if source is None:
                return None
            facts = self._facts_for(source.path)
            if facts is None or facts.opaque:
                return None
            if name in facts.definitions:
                line, column = facts.definitions[name]
                return _Definition(module, name, source.path, line, column)
            target = facts.reexports.get(name)
            if target is None:
                return None
            if target in self._files.sources or target in self._files.packages:
                return None  # a module, rather than a symbol
            module, _, name = target.rpartition(".")
        return None

    def _facts_for(self, path: str) -> _ModuleFacts | None:
        """Cached per content, so unchanged modules are never parsed again."""
        if path not in self._facts:
            self._facts[path] = self._load_facts(path)
        return self._facts[path]

    def _load_facts(self, path: str) -> _ModuleFacts | None:
        files = self._files
        source = files.source_for_path(path)
        if source is None and path not in files.tests:
            return None

        def build() -> _ModuleFacts:
            syntax = self._syntax.for_path(path)
            if syntax is None:
                raise LookupError(path)
            return _module_facts(syntax)

        cache = self._syntax.cache
        if cache is None:
            return build()
        return cache.get(
            "facade-module",
            [
                path,
                source.module if source else None,
                source.is_package if source else None,
                files.content_id(path),
            ],
            _FACTS,
            build=build,
        )

    def _cycle(self, package: str, module: str) -> tuple[str, ...]:
        previous: dict[str, str | None] = {module: None}
        queue = deque([module])
        while queue:
            current = queue.popleft()
            for child in sorted(self._adjacency.get(current, ())):
                if child in previous:
                    continue
                previous[child] = current
                if child == package:
                    chain = [package]
                    while (parent := previous[chain[-1]]) is not None:
                        chain.append(parent)
                    return (package, *reversed(chain))
                queue.append(child)
        return ()


def _module_facts(module: ModuleSyntax) -> _ModuleFacts:
    bindings = _static_bindings(module)
    tree = module.tree
    return replace(
        bindings,
        public=_public_names(tree),
        declares_all=any(
            isinstance(node, ast.Name) and node.id == "__all__" for node in module.nodes
        ),
        imports=tuple(_runtime_imports(module)),
    )


def _runtime_imports(module: ModuleSyntax) -> Iterator[tuple[str, tuple[str, ...]]]:
    guarded = module.guarded_lines
    for node in module.imports:
        if not isinstance(node, ast.ImportFrom) or node.lineno in guarded:
            continue
        base = resolve_relative(
            module.module,
            is_package=module.is_package,
            level=node.level,
            target=node.module,
        )
        yield (
            base,
            tuple(
                alias.name
                for alias in node.names
                if not alias.name.startswith("_") and alias.name != "*"
            ),
        )


def _static_bindings(module: ModuleSyntax) -> _ModuleFacts:
    lazy = static_lazy_exports(module.tree)
    definitions = definition_bindings(module.tree)
    imports: dict[str, list[ast.AST]] = defaultdict(list)
    for node in module.nodes:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"exec", "eval", "globals", "locals", "vars"}
            and lazy is None
        ):
            return _ModuleFacts(module.module, opaque=True)
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return _ModuleFacts(module.module, opaque=True)
                name = alias.asname or (
                    alias.name.split(".")[0]
                    if isinstance(node, ast.Import)
                    else alias.name
                )
                imports[name].append(node)
    known: dict[str, _Location | str] = {}
    for node in module.tree.body:
        if isinstance(node, ast.ImportFrom):
            base = resolve_relative(
                module.module,
                is_package=module.is_package,
                level=node.level,
                target=node.module,
            )
            for alias in node.names:
                name = alias.asname or alias.name
                if name not in definitions and len(imports[name]) == 1:
                    known[name] = f"{base}.{alias.name}"
        else:
            for name in _definition_names(node):
                if len(definitions.get(name, ())) == 1 and name not in imports:
                    known[name] = _Location(node.lineno, node.col_offset + 1)
    known.update(
        {
            name: target
            for name, target in (lazy or {}).items()
            if name not in known and name not in definitions and name not in imports
        }
    )
    return _ModuleFacts(
        module.module,
        opaque="__getattr__" in definitions and lazy is None,
        definitions={
            name: value for name, value in known.items() if isinstance(value, _Location)
        },
        reexports={
            name: value for name, value in known.items() if isinstance(value, str)
        },
        bound=frozenset(definitions) | frozenset(imports) | frozenset(lazy or {}),
    )


def _public_names(tree: ast.Module) -> frozenset[str] | None:
    names = explicit_exports(tree)
    lazy = static_lazy_exports(tree)
    return frozenset(lazy) if names is None and lazy is not None else names


def _definition_names(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [target.id for target in node.targets if isinstance(target, ast.Name)]
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.value is not None
    ):
        return [node.target.id]
    if isinstance(node, ast.TypeAlias):
        return [node.name.id]
    return []
