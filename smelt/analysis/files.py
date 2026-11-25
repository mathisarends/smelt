from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from smelt.analysis import native
from smelt.analysis.errors import AnalysisError
from smelt.config.patterns import path_matches
from smelt.config.source import SourceStore

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping

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


@dataclass(frozen=True)
class FileIndex:
    root: Path
    config: SmeltConfig
    sources: dict[str, SourceFile] = field(default_factory=dict)
    packages: dict[str, PackageDir] = field(default_factory=dict)
    package_parts: dict[str, list[PackageDir]] = field(default_factory=dict)
    package_roots: dict[str, list[Path]] = field(default_factory=dict)
    tests: dict[str, TestFile] = field(default_factory=dict)
    missing_root_packages: list[str] = field(default_factory=list)
    # YAML files by name alone, listed when a file naming policy reads them.
    yaml_paths: tuple[str, ...] = ()
    store: SourceStore = field(default_factory=SourceStore, repr=False)
    _text_cache: dict[str, str] = field(default_factory=dict, repr=False)
    _line_cache: dict[str, list[str]] = field(default_factory=dict, repr=False)
    # The SHA-256 of texts another reader checked, by path.
    _observed: dict[str, str] = field(default_factory=dict, repr=False)
    _directories: dict[Path, tuple[tuple[Path, bool], ...]] = field(
        default_factory=dict, repr=False
    )
    _relative_paths: dict[Path, str] = field(default_factory=dict, repr=False)
    _resolved_paths: dict[Path, Path] = field(default_factory=dict, repr=False)
    _test_root_paths: dict[str, bool] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.store.resolve(self.root))

    @classmethod
    def discover(
        cls, root: Path, config: SmeltConfig, *, store: SourceStore | None = None
    ) -> FileIndex:
        """The files the native core finds in the project below `root`."""
        store = store or SourceStore()
        project = config.project
        naming = config.structure.file_naming
        inventory = native.request(
            "--internal-files",
            {
                "root": str(store.resolve(root)),
                "root_packages": project.root_packages,
                "source_roots": project.source_roots,
                "test_roots": project.test_roots,
                "exclude": list(project.exclude_patterns),
                "yaml": naming.yaml_extension is not None
                or naming.compose_basename is not None,
            },
        )
        if "error" in inventory:
            raise AnalysisError(inventory["error"])
        return cls.from_inventory(root, config, inventory, store=store)

    @classmethod
    def from_inventory(
        cls,
        root: Path,
        config: SmeltConfig,
        inventory: Mapping[str, Any],
        *,
        store: SourceStore | None = None,
    ) -> FileIndex:
        """Index the sources, packages and tests the native core reported."""
        store = store or SourceStore()
        base = store.resolve(root)
        packages: dict[str, PackageDir] = {}
        parts: dict[str, list[PackageDir]] = {}
        for entry in inventory["packages"]:
            portion = PackageDir(
                entry["module"], entry["path"], entry["source_root"], entry["has_init"]
            )
            packages.setdefault(portion.module, portion)
            parts.setdefault(portion.module, []).append(portion)
        return cls(
            root=base,
            config=config,
            sources={
                entry["module"]: SourceFile(
                    entry["module"],
                    entry["path"],
                    base / entry["path"],
                    is_package=entry["is_package"],
                )
                for entry in inventory["sources"]
            },
            packages=packages,
            package_parts=parts,
            package_roots={
                package: [store.resolve(base / part.path) for part in parts[package]]
                for package in config.project.root_packages
                if package in parts
            },
            tests={
                entry["path"]: TestFile(
                    entry["path"], base / entry["path"], entry["test_root"]
                )
                for entry in inventory["tests"]
            },
            missing_root_packages=list(inventory["missing_root_packages"]),
            yaml_paths=tuple(inventory["yaml_paths"]),
            store=store,
        )

    def is_excluded(self, path: str) -> bool:
        return any(
            path_matches(pattern, path)
            for pattern in self.config.project.exclude_patterns
        )

    def relative(self, path: Path) -> str:
        cached = self._relative_paths.get(path)
        if cached is not None:
            return cached
        absolute = self.resolve_path(path)
        try:
            relative = absolute.relative_to(self.root).as_posix()
        except ValueError:
            try:
                relative_path = Path(os.path.relpath(absolute, self.root))
                relative = relative_path.as_posix()
            except ValueError:  # different Windows drives
                relative = absolute.as_posix()
        self._relative_paths[path] = relative
        return relative

    def resolve_path(self, path: Path) -> Path:
        resolved = self._resolved_paths.get(path)
        if resolved is None:
            resolved = self.store.resolve(path)
            self._resolved_paths[path] = resolved
        return resolved

    @cached_property
    def by_path(self) -> dict[str, SourceFile]:
        return {source.path: source for source in self.sources.values()}

    def source_for_path(self, path: str) -> SourceFile | None:
        return self.by_path.get(path)

    def is_test_path(self, path: str) -> bool:
        """Recognize tests even when a test root overlaps a source package."""
        return path in self.tests or self._under_test_root(path)

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
            expected = self._observed.get(path)
            if expected and hashlib.sha256(cached.encode()).hexdigest() != expected:
                msg = "source changed since the check; run the check again"
                raise AnalysisError(msg)
            self._text_cache[path] = cached
        return cached

    def observe(self, digests: Mapping[str, str]) -> None:
        """Record the texts another reader checked, so a later read rejects edits since."""
        self._observed.update(digests)

    def lines(self, path: str) -> list[str]:
        if path not in self._line_cache:
            self._line_cache[path] = self.read_text(path).splitlines()
        return self._line_cache[path]

    def line(self, path: str, number: int) -> str:
        lines = self.lines(path)
        return lines[number - 1] if 0 < number <= len(lines) else ""

    def sha256(self, path: str) -> str:
        return hashlib.sha256(self.read_text(path).encode()).hexdigest()

    def size(self, path: str) -> int:
        return len(self.read_text(path).encode())

    def all_python_paths(self) -> Iterator[str]:
        yield from (source.path for source in self.sources.values())
        yield from self.tests

    def all_check_paths(self) -> Iterator[str]:
        yield from self.all_python_paths()
        yield from self.yaml_paths

    def entries(self, directory: Path) -> tuple[tuple[Path, bool], ...]:
        """Share directory entries within one discovered view of the project."""
        if directory not in self._directories:
            self._directories[directory] = tuple(_entries(self.store, directory))
        return self._directories[directory]

    def python_files(self, directory: Path) -> Iterator[Path]:
        yield from _walk_python_files(
            directory, resolve=self.resolve_path, entries=self.entries
        )

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

    def _under_test_root(self, path: str) -> bool:
        # Import-policy rules ask once per import and contract.
        under = self._test_root_paths.get(path)
        if under is None:
            under = any(
                Path(path).is_relative_to(root)
                for root in self.config.project.test_roots
            )
            self._test_root_paths[path] = under
        return under


def python_files(
    directory: Path, *, store: SourceStore | None = None
) -> Iterable[Path]:
    """Python files below a root, skipping tool directories and symlink cycles."""
    store = store or SourceStore()
    yield from _walk_python_files(
        directory, resolve=store.resolve, entries=lambda parent: _entries(store, parent)
    )


def _entries(store: SourceStore, directory: Path) -> Iterator[tuple[Path, bool]]:
    for entry in store.iterdir(directory):
        yield entry, store.is_dir(entry)


def _walk_python_files(
    directory: Path,
    *,
    resolve: Callable[[Path], Path],
    entries: Callable[[Path], Iterable[tuple[Path, bool]]],
) -> Iterator[Path]:
    visited: set[Path] = set()
    pending = [directory]
    while pending:
        parent = pending.pop()
        resolved = resolve(parent)
        if resolved in visited:
            continue
        visited.add(resolved)
        for entry, is_dir in entries(parent):
            if is_dir:
                if entry.name not in _SKIP_DIRS and not entry.name.startswith("."):
                    pending.append(entry)
            elif entry.suffix == ".py":
                yield entry
