from __future__ import annotations

import ast
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import grimp
from grimp import _rustgrimp as rust  # type: ignore[attr-defined]
from grimp.adaptors.filesystem import FileSystem
from grimp.application.config import settings as grimp_settings
from grimp.application.ports.modulefinder import (
    AbstractModuleFinder,
    FoundPackage,
    ModuleFile,
)
from grimp.application.ports.packagefinder import AbstractPackageFinder
from grimp.domain.valueobjects import Module
from grimp.exceptions import SourceSyntaxError
from pydantic import TypeAdapter

from smelt.analysis.import_cache import import_cache_type, resolution_identity
from smelt.analysis.parsing import (
    AnalysisError,
    AstCache,
    python_note,
    resolve_relative,
    type_checking_lines,
)
from smelt.analysis.parsing import (
    is_stdlib as is_stdlib,  # noqa: PLC0414 - preserve the public import helper
)
from smelt.model import is_within

if TYPE_CHECKING:
    from collections.abc import Iterator

    from grimp.application.ports.filesystem import AbstractFileSystem, BasicFileSystem

    from smelt.analysis.cache import FactCache
    from smelt.analysis.files import FileIndex


class _SourceFileSystem(FileSystem):
    def __init__(self, files: FileIndex) -> None:
        self.files = files
        self._basic: BasicFileSystem | None = None

    def read(self, file_name: str) -> str:
        return self.files.store.read_text(Path(file_name), encoding="utf-8-sig")

    def exists(self, file_name: str) -> bool:
        return self.files.store.is_file(Path(file_name))

    def get_mtime(self, file_name: str) -> float:
        identity = self.files.store.identity(Path(file_name))
        if identity is not None:
            return float(int(identity[-12:], 16))
        return super().get_mtime(file_name)

    def convert_to_basic(self) -> BasicFileSystem:
        if self._basic is not None:
            return self._basic
        inventory = self.files.store.inventory()
        if inventory is None:
            return super().convert_to_basic()
        # Native Grimp accepts its in-memory filesystem. Include unselected
        # filenames for import resolution without parsing their source.
        contents = {path.as_posix(): "" for path in inventory if path.suffix == ".py"}
        basic = rust.FakeBasicFileSystem(content_map=contents)
        # FakeBasicFileSystem's constructor strips indentation from its test
        # fixtures. write() preserves the exact analyzed source instead.
        for source in self.files.sources.values():
            basic.write(source.absolute.as_posix(), self.files.read_text(source.path))
        self._basic = cast("BasicFileSystem", basic)
        return self._basic

    def write(self, file_name: str, contents: str) -> None:
        raise OSError("analysis sources are read-only")


@dataclass(frozen=True, slots=True)
class ImportDetail:
    importer: str
    # Grimp's resolved target; external packages are squashed to their top level.
    imported: str
    line: int
    line_contents: str
    # Fully qualified names the statement imports, e.g. ``sqlalchemy.orm.Session``.
    names: tuple[str, ...]
    type_checking: bool
    external: bool
    column: int
    end_column: int

    @property
    def top_level(self) -> str:
        return self.imported.split(".")[0]


_DETAILS = TypeAdapter(list[ImportDetail])
_CHAIN_CACHE_HOPS = 20_000


class _SourceRootPackageFinder(AbstractPackageFinder):
    def __init__(self, files: FileIndex) -> None:
        self._files = files

    def determine_package_directories(
        self, package_name: str, file_system: AbstractFileSystem
    ) -> set[str]:
        return {
            self._files.resolve_path(path).as_posix()
            for path in self._files.package_roots[package_name]
        }


class _IndexedModuleFinder(AbstractModuleFinder):
    """Give Grimp the same modules and namespace packages as Smelt's file index."""

    def __init__(self, files: FileIndex) -> None:
        self._files = files

    def find_package(
        self,
        package_name: str,
        package_directory: str,
        file_system: AbstractFileSystem,
    ) -> FoundPackage:
        directory = Path(package_directory)
        module_files = frozenset(
            ModuleFile(
                Module(source.module), file_system.get_mtime(str(source.absolute))
            )
            for source in self._files.sources.values()
            if is_within(source.module, package_name)
            and source.absolute.is_relative_to(directory)
        )
        namespace_packages = frozenset(
            package.module
            for portions in self._files.package_parts.values()
            for package in portions
            if is_within(package.module, package_name)
            and not package.has_init
            and (self._files.root / package.path).is_relative_to(directory)
        )
        return FoundPackage(
            name=package_name,
            directory=package_directory,
            module_files=module_files,
            namespace_packages=namespace_packages,
        )


class ImportIndex:
    def __init__(
        self,
        graph: grimp.ImportGraph,
        files: FileIndex,
        asts: AstCache,
        *,
        cache: FactCache | None = None,
        inventory: str = "",
    ) -> None:
        self.graph = graph
        self.files = files
        self._asts = asts
        self._cache = cache
        self._inventory = inventory
        roots = files.config.project.root_packages
        self.first_party = frozenset(
            m for m in graph.modules if any(is_within(m, r) for r in roots)
        )
        self._outgoing: dict[str, list[ImportDetail]] = {}
        self._incoming: dict[str, list[ImportDetail]] | None = None
        self._chains: dict[tuple[str, bool], tuple[tuple[ImportDetail, ...], ...]] = {}
        self._chain_hops = 0

    @classmethod
    def build(
        cls, files: FileIndex, asts: AstCache, *, cache: FactCache | None = None
    ) -> ImportIndex:
        project = files.config.project
        packages = [
            p for p in project.root_packages if p not in files.missing_root_packages
        ]
        if not packages:
            msg = "none of project.root_packages were found under project.source_roots"
            raise AnalysisError(msg)
        inventory = resolution_identity(files)
        previous_package_finder = grimp_settings.PACKAGE_FINDER
        previous_module_finder = grimp_settings.MODULE_FINDER
        previous_file_system = grimp_settings.FILE_SYSTEM
        previous_cache_class = grimp_settings.CACHE_CLASS
        grimp_settings.configure(
            PACKAGE_FINDER=_SourceRootPackageFinder(files),
            MODULE_FINDER=_IndexedModuleFinder(files),
            FILE_SYSTEM=_SourceFileSystem(files),
            CACHE_CLASS=import_cache_type(files, asts, cache, inventory),
        )
        try:
            graph = grimp.build_graph(
                packages[0],
                *packages[1:],
                include_external_packages=True,
                cache_dir="smelt",
            )
        except SourceSyntaxError as exc:
            raise AnalysisError(
                str(exc) + python_note(files.root, store=files.store)
            ) from exc
        finally:
            grimp_settings.configure(
                PACKAGE_FINDER=previous_package_finder,
                MODULE_FINDER=previous_module_finder,
                FILE_SYSTEM=previous_file_system,
                CACHE_CLASS=previous_cache_class,
            )
        for module in list(graph.modules):
            if (
                any(is_within(module, p) for p in packages)
                and module not in files.sources
                and module not in files.packages
            ):
                graph.remove_module(module)
        missing = files.sources.keys() - graph.modules
        if missing:
            examples = ", ".join(sorted(missing)[:3])
            raise AnalysisError(
                f"import graph omitted {len(missing)} source module(s): {examples}"
            )
        return cls(graph, files, asts, cache=cache, inventory=inventory)

    def is_external(self, module: str) -> bool:
        return module not in self.first_party

    def modules(self) -> list[str]:
        return sorted(m for m in self.first_party if m in self.files.sources)

    def imports_of(self, module: str) -> list[ImportDetail]:
        cached = self._outgoing.get(module)
        if cached is None:
            source = self.files.sources.get(module)
            cached = (
                self._details_for(module)
                if self._cache is None or source is None
                else self._cache.get(
                    "import-details",
                    [
                        module,
                        source.path,
                        source.is_package,
                        self.files.content_id(source.path),
                        self._inventory,
                    ],
                    _DETAILS,
                    lambda: self._details_for(module),
                )
            )
            self._outgoing[module] = cached
        return cached

    def importers_of(self, module: str) -> list[ImportDetail]:
        if self._incoming is None:
            incoming: dict[str, list[ImportDetail]] = defaultdict(list)
            for detail in self.all_imports():
                incoming[detail.imported].append(detail)
            self._incoming = dict(incoming)
        return self._incoming.get(module, [])

    def all_imports(self) -> Iterator[ImportDetail]:
        for module in self.modules():
            yield from self.imports_of(module)

    def first_party_edges(
        self, *, include_type_checking: bool = True
    ) -> dict[str, set[str]]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for detail in self.all_imports():
            if detail.external or (detail.type_checking and not include_type_checking):
                continue
            adjacency[detail.importer].add(detail.imported)
        return adjacency

    def shortest_chains_from(
        self, module: str, *, include_type_checking: bool = True
    ) -> Iterator[tuple[ImportDetail, ...]]:
        """Share deterministic witnesses between rules within this graph view."""
        key = module, include_type_checking
        if key in self._chains:
            yield from self._chains[key]
            return
        paths: list[tuple[ImportDetail, ...]] = []
        hops = 0
        for path in self._shortest_chains_from(
            module, include_type_checking=include_type_checking
        ):
            hops += len(path)
            if hops + self._chain_hops <= _CHAIN_CACHE_HOPS:
                paths.append(path)
            else:
                paths.clear()
            yield path
        if hops + self._chain_hops <= _CHAIN_CACHE_HOPS:
            self._chains[key] = tuple(paths)
            self._chain_hops += hops

    def _shortest_chains_from(
        self, module: str, *, include_type_checking: bool
    ) -> Iterator[tuple[ImportDetail, ...]]:
        """Shortest import paths, including external leaves, without revisiting cycles.

        Keep every external statement: Grimp squashes external submodules, but
        policies may allow ``sqlalchemy.orm`` while denying ``sqlalchemy.sql``.
        Traverse modules in lexical order and prefer the earliest active import
        for a deterministic witness. No third-party source is inspected.
        """
        previous: dict[str, ImportDetail] = {}
        visited = {module}
        queue = deque([module])
        while queue:
            current = queue.popleft()
            for detail in sorted(
                self.imports_of(current),
                key=lambda d: (d.imported, d.line, d.column),
            ):
                if detail.type_checking and not include_type_checking:
                    continue
                if not detail.external:
                    if detail.imported in visited:
                        continue
                    visited.add(detail.imported)
                    previous[detail.imported] = detail
                    queue.append(detail.imported)
                path = [detail]
                while path[-1].importer != module:
                    path.append(previous[path[-1].importer])
                yield tuple(reversed(path))

    def _details_for(self, module: str) -> list[ImportDetail]:
        source = self.files.sources.get(module)
        if source is None or module not in self.first_party:
            return []
        tree = self._asts.parse(source.path)
        statements = _statements_by_line(tree)
        guarded = type_checking_lines(tree)
        details: list[ImportDetail] = []
        for imported in sorted(self.graph.find_modules_directly_imported_by(module)):
            external = imported not in self.first_party
            raw_lines = {
                int(raw["line_number"]): str(raw["line_contents"])
                for raw in self.graph.get_import_details(
                    importer=module, imported=imported
                )
            }
            for line, contents in sorted(raw_lines.items()):
                nodes: list[ast.Import | ast.ImportFrom | None] = [
                    node
                    for node in statements.get(line, [])
                    if _matches_statement(node, module, source.is_package, imported)
                ]
                for node in nodes or [None]:
                    names, column, end_column = _describe(
                        node, module, source.is_package, imported, contents
                    )
                    details.append(
                        ImportDetail(
                            importer=module,
                            imported=imported,
                            line=line,
                            line_contents=contents,
                            names=names,
                            type_checking=line in guarded,
                            external=external,
                            column=column,
                            end_column=end_column,
                        )
                    )
        details.sort(key=lambda d: (d.line, d.column, d.imported))
        return details


def _statements_by_line(
    tree: ast.Module,
) -> dict[int, list[ast.Import | ast.ImportFrom]]:
    statements: dict[int, list[ast.Import | ast.ImportFrom]] = defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            statements[node.lineno].append(node)
    return statements


def _matches_statement(
    node: ast.Import | ast.ImportFrom, module: str, is_package: bool, imported: str
) -> bool:
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
    else:
        base = resolve_relative(
            module, is_package=is_package, level=node.level, target=node.module
        )
        names = [
            base if alias.name == "*" else f"{base}.{alias.name}"
            for alias in node.names
        ]
    return any(is_within(name, imported) or is_within(imported, name) for name in names)


def _describe(
    node: ast.Import | ast.ImportFrom | None,
    module: str,
    is_package: bool,
    imported: str,
    line_contents: str,
) -> tuple[tuple[str, ...], int, int]:
    """Imported names and the 1-based column span to underline on the first line."""
    if node is None:
        return (imported,), 1, len(line_contents) + 1
    if isinstance(node, ast.Import):
        return _describe_import(node, imported, line_contents)
    return _describe_import_from(node, module, is_package, imported)


def _describe_import(
    node: ast.Import, imported: str, line_contents: str
) -> tuple[tuple[str, ...], int, int]:
    first_line_end = (
        node.end_col_offset if node.end_lineno == node.lineno else len(line_contents)
    )
    span = (node.col_offset + 1, (first_line_end or 0) + 1)
    matching = [
        alias
        for alias in node.names
        if is_within(alias.name, imported) or is_within(imported, alias.name)
    ]
    names = tuple(alias.name for alias in matching) or (imported,)
    if len(matching) == 1 and matching[0].lineno == node.lineno:
        alias = matching[0]
        span = (alias.col_offset + 1, (alias.end_col_offset or 0) + 1)
    return names, *span


def _describe_import_from(
    node: ast.ImportFrom, module: str, is_package: bool, imported: str
) -> tuple[tuple[str, ...], int, int]:
    base = resolve_relative(
        module, is_package=is_package, level=node.level, target=node.module
    )
    names = tuple(
        f"{base}.{alias.name}" if base else alias.name for alias in node.names
    )
    for alias, name in zip(node.names, names, strict=True):
        if name == imported and alias.lineno == node.lineno:
            return (name,), alias.col_offset + 1, (alias.end_col_offset or 0) + 1
    matching = tuple(
        n for n in names if is_within(n, imported) or is_within(imported, n)
    )
    if len(node.names) == 1 and node.names[0].lineno == node.lineno:
        alias = node.names[0]
        return matching or names, alias.col_offset + 1, (alias.end_col_offset or 0) + 1
    start = node.col_offset + len("from ")
    text = "." * node.level + (node.module or "")
    return matching or names, start + 1, start + len(text) + 1
