"""Bounded symbol provenance and runtime demand for package facades."""

from __future__ import annotations

import ast
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smelt.analysis.exports import MAX_HOPS
from smelt.analysis.function_usage import definition_bindings, explicit_exports
from smelt.analysis.lazy_exports import static_lazy_exports
from smelt.analysis.parsing import resolve_relative, type_checking_lines
from smelt.model import is_within

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.imports import ImportIndex
    from smelt.analysis.syntax import ModuleSyntax, SyntaxIndex


@dataclass(frozen=True)
class _Definition:
    module: str
    name: str
    path: str
    line: int
    column: int


@dataclass
class _Bindings:
    known: dict[str, ast.stmt | str] = field(default_factory=dict)
    bound: set[str] = field(default_factory=set)
    opaque: bool = False


@dataclass(frozen=True)
class _Use:
    definition: _Definition
    importer: str
    test: bool


@dataclass(frozen=True)
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
    def __init__(
        self, syntax: SyntaxIndex, imports: ImportIndex, *, count_tests: bool
    ) -> None:
        self._syntax = syntax
        self._bindings: dict[str, _Bindings] = {}
        self._resolved: dict[tuple[str, str], _Definition | None] = {}
        self._uses = tuple(self._collect_uses(count_tests=count_tests))
        self._exports = self._collect_exports()
        self._adjacency = imports.first_party_edges(include_type_checking=False)

    def canonical_exports(
        self, declarations: dict[str, str]
    ) -> tuple[dict[str, str], list[tuple[str, str]]]:
        """Verify explicit decisions against identity and actual static exports."""
        verified: dict[str, str] = {}
        issues: list[tuple[str, str]] = []
        owners: dict[_Definition, str] = {}
        for symbol, export in sorted(declarations.items()):
            module, _, name = symbol.rpartition(".")
            definition = self._resolve(module, name)
            owner, _, public_name = export.rpartition(".")
            target = self._resolve(owner, public_name)
            if definition is None:
                issues.append(
                    (
                        symbol,
                        f"cannot statically resolve {symbol}; use an analyzed, unambiguous definition or static re-export",
                    )
                )
            elif target != definition or export not in self._exports.get(
                definition, ()
            ):
                issues.append(
                    (
                        symbol,
                        f"{export} is not a verified public export of {symbol}; declare a static re-export and include it in __all__ when declared. Unsupported dynamic exports cannot verify ownership.",
                    )
                )
            elif definition in owners and owners[definition] != export:
                issues.append(
                    (
                        symbol,
                        f"conflicting canonical exports for {definition.module}.{definition.name}: {owners[definition]} and {export}; choose one public owner",
                    )
                )
            else:
                owners[definition] = export
                verified[f"{definition.module}.{definition.name}"] = export
        return verified, issues

    def gaps(self, package: str, *, count_tests: bool) -> Iterator[FacadeGap]:
        source = self._syntax.files.sources.get(package)
        if source is None or not source.is_package:
            return
        facade = self._syntax.for_path(source.path)
        if facade is None:
            return
        bindings = self._for_module(package)
        exports = _public_names(facade.tree)
        if bindings.opaque or exports is None:
            return
        declares_all = _declares_all(facade.tree)
        callers: dict[_Definition, tuple[set[str], set[str]]] = {}
        for use in self._uses:
            definition = use.definition
            if (
                (use.test and not count_tests)
                or definition.module == package
                or not is_within(definition.module, package)
                or is_within(use.importer, package)
            ):
                continue
            groups = callers.setdefault(definition, (set(), set()))
            groups[1 if use.test else 0].add(use.importer)
        for definition, (sources, tests) in sorted(
            callers.items(), key=lambda item: (item[0].module, item[0].name)
        ):
            name = definition.name
            existing = self._resolve(package, name)
            if existing == definition and (not declares_all or name in exports):
                continue
            # A conflicting/uncertain binding must never suggest overwriting an API.
            if existing != definition and (name in bindings.bound or name in exports):
                continue
            yield FacadeGap(
                definition.module,
                name,
                definition.path,
                definition.line,
                definition.column,
                tuple(sorted(sources)),
                tuple(sorted(tests)),
                tuple(
                    sorted(self._exports.get(definition, set()) - {f"{package}.{name}"})
                ),
                self._cycle(package, definition.module),
                existing != definition,
            )

    def _for_module(self, module: str) -> _Bindings:
        if module not in self._bindings:
            source = self._syntax.files.sources.get(module)
            syntax = self._syntax.for_path(source.path) if source else None
            self._bindings[module] = (
                _static_bindings(syntax) if syntax else _Bindings(opaque=True)
            )
        return self._bindings[module]

    def _resolve(self, module: str, name: str) -> _Definition | None:
        key = module, name
        if key not in self._resolved:
            self._resolved[key] = self._follow(module, name)
        return self._resolved[key]

    def _follow(self, module: str, name: str) -> _Definition | None:
        seen: set[tuple[str, str]] = set()
        while len(seen) < MAX_HOPS and (module, name) not in seen:
            seen.add((module, name))
            bindings = self._for_module(module)
            if bindings.opaque:
                return None
            binding = bindings.known.get(name)
            if isinstance(binding, str):
                if (
                    binding in self._syntax.files.sources
                    or binding in self._syntax.files.packages
                ):
                    return None  # a module, rather than a symbol
                module, _, name = binding.rpartition(".")
            elif binding is not None:
                source = self._syntax.files.sources[module]
                return _Definition(
                    module, name, source.path, binding.lineno, binding.col_offset + 1
                )
            else:
                return None
        return None

    def _collect_uses(self, *, count_tests: bool) -> Iterator[_Use]:
        files = self._syntax.files
        test_roots = tuple(
            root.rstrip("/") + "/" for root in files.config.project.test_roots
        )
        paths = [
            (source.path, source.path.startswith(test_roots))
            for source in files.sources.values()
        ]
        if count_tests:
            paths.extend((path, True) for path in files.tests)
        for path, test in paths:
            if test and not count_tests:
                continue
            syntax = self._syntax.for_path(path)
            if syntax is None:
                continue
            guarded = type_checking_lines(syntax.tree)
            for node in ast.walk(syntax.tree):
                if not isinstance(node, ast.ImportFrom) or node.lineno in guarded:
                    continue
                base = resolve_relative(
                    syntax.module,
                    is_package=syntax.is_package,
                    level=node.level,
                    target=node.module,
                )
                source = files.sources.get(base)
                if source is None or source.is_package:
                    continue  # published facade imports do not represent deep coupling
                for alias in node.names:
                    if alias.name.startswith("_") or alias.name == "*":
                        continue
                    definition = self._resolve(base, alias.name)
                    if definition is not None and not definition.name.startswith("_"):
                        yield _Use(definition, syntax.module, test)

    def _collect_exports(self) -> dict[_Definition, set[str]]:
        result: dict[_Definition, set[str]] = defaultdict(set)
        for source in self._syntax.files.sources.values():
            syntax = self._syntax.for_path(source.path)
            if syntax is None:
                continue
            exports = _public_names(syntax.tree)
            if exports is None or (
                not source.is_package and not _declares_all(syntax.tree)
            ):
                continue
            for name in self._for_module(source.module).known:
                if name.startswith("_") or (
                    _declares_all(syntax.tree) and name not in exports
                ):
                    continue
                definition = self._resolve(source.module, name)
                if definition is not None:
                    result[definition].add(f"{source.module}.{name}")
        return result

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


def _declares_all(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id == "__all__" for node in ast.walk(tree)
    )


def _static_bindings(module: ModuleSyntax) -> _Bindings:
    lazy = static_lazy_exports(module.tree)
    definitions = definition_bindings(module.tree)
    imports: dict[str, list[ast.AST]] = defaultdict(list)
    for node in ast.walk(module.tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"exec", "eval", "globals", "locals", "vars"}
            and lazy is None
        ):
            return _Bindings(opaque=True)
        if isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return _Bindings(opaque=True)
                name = alias.asname or (
                    alias.name.split(".")[0]
                    if isinstance(node, ast.Import)
                    else alias.name
                )
                imports[name].append(node)
    result = _Bindings(
        bound=set(definitions) | set(imports) | set(lazy or {}),
        opaque="__getattr__" in definitions and lazy is None,
    )
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
                    result.known[name] = f"{base}.{alias.name}"
        else:
            for name in _definition_names(node):
                if len(definitions.get(name, ())) == 1 and name not in imports:
                    result.known[name] = node
    result.known.update(
        {
            name: target
            for name, target in (lazy or {}).items()
            if name not in result.known
            and name not in definitions
            and name not in imports
        }
    )
    return result


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
