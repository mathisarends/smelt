from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from smelt.analysis.errors import AnalysisError
from smelt.config.patterns import path_matches
from smelt.config.source import SourceStore

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from smelt.config.models import SmeltConfig

_SKIP_DIRS = frozenset({"__pycache__", ".git", ".venv", "venv", "node_modules"})


@dataclass(frozen=True, slots=True)
class SourceFile:
    module: str
    path: str  # POSIX, relative to the project root
    absolute: Path
    is_package: bool  # an ``__init__.py``


@dataclass(frozen=True, slots=True)
class TestFile:
    path: str
    absolute: Path
    test_root: str

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def is_conftest(self) -> bool:
        return self.name == "conftest.py"


@dataclass(slots=True)
class PackageDir:
    module: str
    path: str
    source_root: str
    has_init: bool


@dataclass
class FileIndex:
    root: Path
    config: SmeltConfig
    sources: dict[str, SourceFile] = field(default_factory=dict)
    packages: dict[str, PackageDir] = field(default_factory=dict)
    package_parts: dict[str, list[PackageDir]] = field(default_factory=dict)
    package_roots: dict[str, list[Path]] = field(default_factory=dict)
    tests: dict[str, TestFile] = field(default_factory=dict)
    missing_root_packages: list[str] = field(default_factory=list)
    store: SourceStore = field(default_factory=SourceStore, repr=False)
    _text_cache: dict[str, str] = field(default_factory=dict, repr=False)
    _line_cache: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _content_ids: dict[str, str] = field(default_factory=dict, repr=False)
    _resolved_paths: dict[Path, Path] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.root = self.store.resolve(self.root)

    @classmethod
    def discover(
        cls, root: Path, config: SmeltConfig, *, store: SourceStore | None = None
    ) -> FileIndex:
        index = cls(root=root, config=config, store=store or SourceStore())
        index._discover_sources()
        index._discover_tests()
        return index

    def is_excluded(self, path: str) -> bool:
        return any(
            path_matches(pattern, path)
            for pattern in self.config.project.exclude_patterns
        )

    def relative(self, path: Path) -> str:
        absolute = self.resolve_path(path)
        try:
            return absolute.relative_to(self.root).as_posix()
        except ValueError:
            try:
                return Path(os.path.relpath(absolute, self.root)).as_posix()
            except ValueError:  # different Windows drives
                return absolute.as_posix()

    def resolve_path(self, path: Path) -> Path:
        resolved = self._resolved_paths.get(path)
        if resolved is None:
            resolved = self.store.resolve(path)
            self._resolved_paths[path] = resolved
        return resolved

    def _discover_sources(self) -> None:
        project = self.config.project
        for package in project.root_packages:
            candidates = {
                self.resolve_path(self.root / source_root / package): source_root
                for source_root in project.source_roots
                if self.store.is_dir(self.root / source_root / package)
                and not self.is_excluded(
                    self.relative(self.root / source_root / package)
                )
            }
            regular = {
                path: source_root
                for path, source_root in candidates.items()
                if self.store.is_file(path / "__init__.py")
            }
            # Python gives an ordinary package precedence over namespace portions,
            # including workspace scaffolding found beneath a broad '.' root.
            selected = regular or candidates
            if len(regular) > 1:
                paths = ", ".join(self.relative(path) for path in regular)
                raise AnalysisError(f"ambiguous package {package}: {paths}")
            self.package_roots[package] = list(selected)
            for directory, source_root in selected.items():
                self._walk_package(directory, package, source_root)
            if not selected:
                self.missing_root_packages.append(package)

    def _walk_package(self, directory: Path, module: str, source_root: str) -> None:
        rel_dir = self.relative(directory)
        if self.is_excluded(rel_dir):
            return
        init = directory / "__init__.py"
        portion = PackageDir(module, rel_dir, source_root, self.store.is_file(init))
        previous = self.packages.get(module)
        if previous is not None and (previous.has_init or portion.has_init):
            raise AnalysisError(
                f"ambiguous package {module}: {previous.path}, {rel_dir}"
            )
        self.packages.setdefault(module, portion)
        self.package_parts.setdefault(module, []).append(portion)
        for entry in sorted(self.store.iterdir(directory), key=lambda p: p.name):
            if self.store.is_dir(entry):
                if entry.name in _SKIP_DIRS or not entry.name.isidentifier():
                    continue
                if _contains_python(entry, store=self.store):
                    self._walk_package(entry, f"{module}.{entry.name}", source_root)
            elif entry.suffix == ".py" and self.store.is_file(entry):
                rel = self.relative(entry)
                if self.is_excluded(rel):
                    continue
                if entry.name == "__init__.py":
                    self._add_source(SourceFile(module, rel, entry, is_package=True))
                elif entry.stem.isidentifier():
                    name = f"{module}.{entry.stem}"
                    self._add_source(SourceFile(name, rel, entry, is_package=False))

    def _add_source(self, source: SourceFile) -> None:
        previous = self.sources.get(source.module)
        if previous is not None:
            raise AnalysisError(
                f"ambiguous module {source.module}: {previous.path}, {source.path}"
            )
        self.sources[source.module] = source

    def _discover_tests(self) -> None:
        source_paths = {source.path for source in self.sources.values()}
        for test_root in self.config.project.test_roots:
            directory = self.root / test_root
            if not self.store.is_dir(directory):
                continue
            for path in sorted(python_files(directory, store=self.store)):
                rel = self.relative(path)
                if rel in source_paths or self.is_excluded(rel):
                    continue
                name = path.name
                if (
                    name == "conftest.py"
                    or name.startswith("test_")
                    or path.stem.endswith("_test")
                ):
                    self.tests[rel] = TestFile(rel, path, test_root.strip("/"))

    @cached_property
    def by_path(self) -> dict[str, SourceFile]:
        return {source.path: source for source in self.sources.values()}

    def source_for_path(self, path: str) -> SourceFile | None:
        return self.by_path.get(path)

    def path_for_module(self, module: str) -> str | None:
        source = self.sources.get(module)
        if source is not None:
            return source.path
        package = self.packages.get(module)
        return package.path if package else None

    def read_text(self, path: str) -> str:
        cached = self._text_cache.get(path)
        if cached is None:
            # Python accepts a UTF-8 BOM, commonly written by Windows editors.
            try:
                cached = self.store.read_text(self.root / path, encoding="utf-8-sig")
            except (OSError, UnicodeError) as exc:
                raise AnalysisError(f"cannot read source {path}: {exc}") from exc
            self._text_cache[path] = cached
        return cached

    def lines(self, path: str) -> list[str]:
        if path not in self._line_cache:
            self._line_cache[path] = self.read_text(path).splitlines()
        return self._line_cache[path]

    def line(self, path: str, number: int) -> str:
        lines = self.lines(path)
        return lines[number - 1] if 0 < number <= len(lines) else ""

    def sha256(self, path: str) -> str:
        return hashlib.sha256(self.read_text(path).encode()).hexdigest()

    def content_id(self, path: str) -> str:
        if path not in self._content_ids:
            self._content_ids[path] = self.store.identity(
                self.root / path
            ) or self.sha256(path)
        return self._content_ids[path]

    def size(self, path: str) -> int:
        return len(self.read_text(path).encode())

    def all_python_paths(self) -> Iterator[str]:
        yield from (source.path for source in self.sources.values())
        yield from self.tests

    def source_root_dir(self, module: str) -> str | None:
        parts = module.split(".")
        for end in range(len(parts), 0, -1):
            package = self.packages.get(".".join(parts[:end]))
            if package is not None:
                return package.source_root
        return None

    def module_to_path(self, module: str, *, package: bool = False) -> str:
        """The expected repo-relative path for ``module``, whether or not it exists."""
        root = (
            self.source_root_dir(module)
            or (self.config.project.source_roots or ["."])[0]
        )
        parts = [p for p in root.strip("/").split("/") if p and p != "."]
        parts.extend(module.split("."))
        return "/".join(parts) + ("/" if package else ".py")


def _contains_python(directory: Path, *, store: SourceStore | None = None) -> bool:
    return any(True for _ in python_files(directory, store=store))


def python_files(
    directory: Path, *, store: SourceStore | None = None
) -> Iterable[Path]:
    """Python files below a root, skipping tool directories and symlink cycles."""
    store = store or SourceStore()
    visited: set[Path] = set()
    pending = [directory]
    while pending:
        parent = pending.pop()
        resolved = store.resolve(parent)
        if resolved in visited:
            continue
        visited.add(resolved)
        for entry in store.iterdir(parent):
            if store.is_dir(entry):
                if entry.name not in _SKIP_DIRS and not entry.name.startswith("."):
                    pending.append(entry)
            elif entry.suffix == ".py":
                yield entry
