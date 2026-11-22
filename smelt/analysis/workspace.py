from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

from smelt.analysis.errors import AnalysisError
from smelt.config.discovery import build_package_settings, packages_in
from smelt.config.source import SourceStore
from smelt.config.workspace import WorkspaceError, member_directories

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkspaceMember:
    """A declared uv workspace member and what ``init`` found in it."""

    path: str  # POSIX, relative to the workspace root
    source_root: str | None = None
    packages: tuple[str, ...] = ()
    namespace: tuple[str, ...] = ()  # the packages without an ``__init__.py``
    skipped: str | None = None  # why nothing was taken from it

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "source_root": self.source_root,
            "packages": list(self.packages),
            "skipped": self.skipped,
        }


class ImportRoot(NamedTuple):
    """A source root, the packages it holds and those without ``__init__.py``."""

    base: Path
    packages: list[Path]
    namespaces: list[Path]


def import_root(
    directory: Path, *, store: SourceStore | None = None
) -> ImportRoot | None:
    """The source root of a project or member, its packages and their namespaces.

    ``src/`` wins when it holds a package; it and a project whose build backend
    declares ``namespace`` or a ``module-name`` may hold namespace packages.
    Otherwise the directory itself, with regular packages only.
    """
    store = store or SourceStore()
    declared, namespace = build_package_settings(directory, store=store)
    for base in (directory / "src", directory):
        allowed = base.name == "src" or namespace or bool(declared)
        found = packages_in(base, namespace=allowed, store=store)
        if declared:
            found = [p for p in found if p.name in declared] or found
        if found:
            namespaces = [p for p in found if not any(store.glob(p, "*.py"))]
            return ImportRoot(base, found, namespaces)
    return None


def workspace_members(
    root: Path, *, strict: bool = False, store: SourceStore | None = None
) -> list[WorkspaceMember]:
    """Every member ``tool.uv.workspace`` declares, with its packages or why it has none."""
    store = store or SourceStore()
    try:
        directories = member_directories(root, strict=strict, store=store)
    except WorkspaceError as exc:
        raise AnalysisError(str(exc)) from exc
    return [
        WorkspaceMember(directory.path, skipped=directory.skipped)
        if directory.skipped is not None
        else _member(root, root / directory.path, store=store)
        for directory in directories
    ]


def _member(root: Path, directory: Path, *, store: SourceStore) -> WorkspaceMember:
    relative = directory.relative_to(root).as_posix()
    located = import_root(directory, store=store)
    if located is None:
        reason = "no Python package in src/ or the member directory"
        return WorkspaceMember(relative, skipped=reason)
    base, packages, namespaces = located
    return WorkspaceMember(
        relative,
        source_root=base.relative_to(root).as_posix(),
        packages=tuple(p.name for p in packages),
        namespace=tuple(p.name for p in namespaces),
    )
