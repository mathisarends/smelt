"""Static re-exports of installed third-party packages, read without importing them."""

from __future__ import annotations

import ast
import hashlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from smelt.analysis.function_usage import definition_bindings
from smelt.analysis.parsing import resolve_relative, tree_nodes

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


class InstalledPackages:
    """Installed package sources on an import search path, parsed on first use.

    The first search-path entry holding a regular package wins, as for imports.
    Stubs take precedence over sources. Namespace packages are not resolved.
    Without a search path, the running interpreter's is used.
    """

    def __init__(self, search_path: Sequence[str] | None = None) -> None:
        self._search_path = tuple(sys.path if search_path is None else search_path)
        self._roots: dict[str, Path | None] = {}
        self._exports: dict[str, dict[str, str]] = {}

    def exports(self, package: str) -> dict[str, str]:
        """Public name -> imported name for one installed package initializer."""
        if package not in self._exports:
            self._exports[package] = {}
            found = self._source(package)
            if found is not None and found[1]:
                path = found[0]
                if (tree := _parse(path)) is not None:
                    self._exports[package] = self._initializer_exports(
                        package, tree, stub=path.suffix == ".pyi"
                    )
        return self._exports[package]

    def identity(self, packages: Iterable[str]) -> dict[str, str | None]:
        """A digest of each package's location and source sizes and modification times.

        None marks a package that is not installed on the search path.
        """
        result: dict[str, str | None] = {}
        for package in sorted(set(packages)):
            root = self._root(package)
            if root is None:
                result[package] = None
                continue
            digest = hashlib.sha256(str(root).encode())
            for path in sorted(
                Path(directory, name)
                for directory, _, names in os.walk(root)
                for name in names
                if name.endswith(_SUFFIXES)
            ):
                stat = path.stat()
                digest.update(
                    f"\0{path.relative_to(root).as_posix()}"
                    f"\0{stat.st_size}\0{stat.st_mtime_ns}".encode()
                )
            result[package] = digest.hexdigest()
        return result

    def _initializer_exports(
        self, package: str, tree: ast.Module, *, stub: bool
    ) -> dict[str, str]:
        declares_all = _declares_all(tree)
        exports = _static_all(tree)
        if declares_all and exports is None:
            return {}
        definitions = definition_bindings(tree)
        # A lazy __getattr__ serves names that only type checkers import.
        lazy = "__getattr__" in definitions and declares_all
        bindings: dict[str, int] = {}
        for node in tree_nodes(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        name = alias.asname or alias.name.split(".")[0]
                        bindings[name] = bindings.get(name, 0) + 1
        candidates: dict[str, list[str]] = {}
        for statement, guarded in _module_statements(tree):
            if not isinstance(statement, ast.ImportFrom) or (guarded and not lazy):
                continue
            base = resolve_relative(
                package, is_package=True, level=statement.level, target=statement.module
            )
            # A re-export of another distribution never shortens an import of this one.
            if base.partition(".")[0] != package.partition(".")[0]:
                continue
            for alias in statement.names:
                if alias.name == "*":
                    for name in self._star_names(base):
                        candidates.setdefault(name, []).append(f"{base}.{name}")
                # Stubs re-export a plain import only through `X as X` or __all__.
                elif not stub or declares_all or alias.asname == alias.name:
                    candidates.setdefault(alias.asname or alias.name, []).append(
                        f"{base}.{alias.name}"
                    )
        return {
            name: targets[0]
            for name, targets in candidates.items()
            if len(targets) == 1
            and bindings.get(name, 0) <= 1
            and not name.startswith("_")
            and name not in definitions
            and (exports is None or name in exports)
        }

    def _star_names(self, module: str) -> frozenset[str]:
        """Names a star import of ``module`` binds, known only from a static __all__."""
        found = self._source(module)
        tree = _parse(found[0]) if found else None
        return (_static_all(tree) if tree else None) or frozenset()

    def _source(self, module: str) -> tuple[Path, bool] | None:
        """The stub or source of ``module`` and whether it is a package."""
        top, *rest = module.split(".")
        root = self._root(top)
        if root is None:
            return None
        directory = root.joinpath(*rest)
        for suffix in _SUFFIXES:
            if (initializer := directory / f"__init__{suffix}").is_file():
                return initializer, True
        if rest:
            for suffix in _SUFFIXES:
                if (path := directory.with_name(rest[-1] + suffix)).is_file():
                    return path, False
        return None

    def _root(self, top: str) -> Path | None:
        if top not in self._roots:
            self._roots[top] = next(
                (
                    Path(entry, top)
                    for entry in self._search_path
                    # An empty entry is the working directory: project code, not a dependency.
                    if entry
                    and any(
                        Path(entry, top, f"__init__{suffix}").is_file()
                        for suffix in _SUFFIXES
                    )
                ),
                None,
            )
        return self._roots[top]


_SUFFIXES = (".pyi", ".py")


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_bytes(), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return None


def _declares_all(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id == "__all__" for node in tree_nodes(tree)
    )


def _static_all(tree: ast.Module) -> frozenset[str] | None:
    """Literal top-level __all__ assignments; None when absent or built dynamically.

    Reading __all__, as a module __dir__ does, leaves it static.
    """
    names: set[str] = set()
    assigned: set[ast.AST] = set()
    for statement in tree.body:
        if not isinstance(statement, ast.Assign | ast.AnnAssign | ast.AugAssign):
            continue
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
        )
        hits = [
            target
            for target in targets
            if isinstance(target, ast.Name) and target.id == "__all__"
        ]
        if not hits:
            continue
        value = statement.value
        if not isinstance(value, ast.List | ast.Tuple | ast.Set) or (
            isinstance(statement, ast.AugAssign)
            and not isinstance(statement.op, ast.Add)
        ):
            return None
        literals = [
            item.value
            for item in value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
        if len(literals) != len(value.elts):
            return None
        if not isinstance(statement, ast.AugAssign):
            names.clear()
        names.update(literals)
        assigned.update(hits)
    for node in tree_nodes(tree):
        if (
            isinstance(node, ast.Name)
            and node.id == "__all__"
            and not isinstance(node.ctx, ast.Load)
            and node not in assigned
        ) or (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "__all__"
        ):
            return None
    return frozenset(names) if assigned else None


def _module_statements(tree: ast.Module) -> list[tuple[ast.stmt, bool]]:
    """Top-level statements, plus those under `if TYPE_CHECKING:` marked guarded."""
    statements: list[tuple[ast.stmt, bool]] = []
    for node in tree.body:
        if isinstance(node, ast.If) and (
            (isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING")
            or (
                isinstance(node.test, ast.Attribute)
                and node.test.attr == "TYPE_CHECKING"
            )
        ):
            statements.extend((child, True) for child in node.body)
        else:
            statements.append((node, False))
    return statements
