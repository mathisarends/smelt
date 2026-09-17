import hashlib
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING

from smelt.config.patterns import path_matches

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

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
    tests: dict[str, TestFile] = field(default_factory=dict)
    missing_root_packages: list[str] = field(default_factory=list)
    _text_cache: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def discover(cls, root: Path, config: SmeltConfig) -> FileIndex:
        index = cls(root=root, config=config)
        index._discover_sources()
        index._discover_tests()
        return index

    def is_excluded(self, path: str) -> bool:
        return any(
            path_matches(pattern, path) for pattern in self.config.project.exclude
        )

    def relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

    def _discover_sources(self) -> None:
        project = self.config.project
        for package in project.root_packages:
            found = False
            for source_root in project.source_roots:
                directory = self.root / source_root / package
                if directory.is_dir():
                    found = True
                    self._walk_package(directory, package, source_root)
                    break
            if not found:
                self.missing_root_packages.append(package)

    def _walk_package(self, directory: Path, module: str, source_root: str) -> None:
        rel_dir = self.relative(directory)
        if self.is_excluded(rel_dir):
            return
        init = directory / "__init__.py"
        self.packages[module] = PackageDir(module, rel_dir, source_root, init.is_file())
        for entry in sorted(directory.iterdir(), key=lambda p: p.name):
            if entry.is_dir():
                if entry.name in _SKIP_DIRS or not entry.name.isidentifier():
                    continue
                if _contains_python(entry):
                    self._walk_package(entry, f"{module}.{entry.name}", source_root)
            elif entry.suffix == ".py" and entry.is_file():
                rel = self.relative(entry)
                if self.is_excluded(rel):
                    continue
                if entry.name == "__init__.py":
                    self.sources[module] = SourceFile(
                        module, rel, entry, is_package=True
                    )
                elif entry.stem.isidentifier():
                    name = f"{module}.{entry.stem}"
                    self.sources[name] = SourceFile(name, rel, entry, is_package=False)

    def _discover_tests(self) -> None:
        source_paths = {source.path for source in self.sources.values()}
        for test_root in self.config.project.test_roots:
            directory = self.root / test_root
            if not directory.is_dir():
                continue
            for path in sorted(_python_files(directory)):
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
            cached = (self.root / path).read_text(encoding="utf-8", errors="replace")
            self._text_cache[path] = cached
        return cached

    def lines(self, path: str) -> list[str]:
        return self.read_text(path).splitlines()

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

    def source_root_dir(self, module: str) -> str | None:
        root = module.split(".", maxsplit=1)[0]
        package = self.packages.get(root)
        return package.source_root if package else None

    def module_to_path(self, module: str, *, package: bool = False) -> str:
        """The expected repo-relative path for ``module``, whether or not it exists."""
        root = (
            self.source_root_dir(module)
            or (self.config.project.source_roots or ["."])[0]
        )
        parts = [p for p in root.strip("/").split("/") if p and p != "."]
        parts.extend(module.split("."))
        return "/".join(parts) + ("/" if package else ".py")


def _contains_python(directory: Path) -> bool:
    return any(True for _ in _python_files(directory))


def _python_files(directory: Path) -> Iterable[Path]:
    for entry in directory.iterdir():
        if entry.is_dir():
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            yield from _python_files(entry)
        elif entry.suffix == ".py":
            yield entry
