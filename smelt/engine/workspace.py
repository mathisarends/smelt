from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from smelt.config.discovery import build_package_settings, packages_in

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


def import_root(directory: Path) -> tuple[Path, list[Path], list[Path]] | None:
    """The source root of a project or member, its packages and their namespaces.

    ``src/`` wins when it holds a package; it and a project whose build backend
    declares ``namespace`` or a ``module-name`` may hold namespace packages.
    Otherwise the directory itself, with regular packages only.
    """
    declared, namespace = build_package_settings(directory)
    for base in (directory / "src", directory):
        allowed = base.name == "src" or namespace or bool(declared)
        found = packages_in(base, namespace=allowed)
        if declared:
            found = [p for p in found if p.name in declared] or found
        if found:
            namespaces = [p for p in found if not any(p.glob("*.py"))]
            return base, found, namespaces
    return None


def workspace_members(root: Path) -> list[WorkspaceMember]:
    """Every member ``tool.uv.workspace`` declares, with its packages or why it has none."""
    workspace = _pyproject(root).get("tool", {}).get("uv", {}).get("workspace", {})
    patterns = _strings(workspace.get("members"))
    excluded = _strings(workspace.get("exclude"))
    resolved_root = root.resolve()
    found: dict[str, WorkspaceMember] = {}
    for pattern in patterns:
        matches = [
            candidate
            for candidate in root.glob(pattern)
            if candidate.is_dir()
            and candidate.resolve().is_relative_to(resolved_root)
            and candidate.resolve() != resolved_root
        ]
        if not matches:
            found.setdefault(
                pattern, WorkspaceMember(pattern, skipped="matches no directory")
            )
        for candidate in matches:
            relative = candidate.relative_to(root).as_posix()
            if any(candidate in root.glob(entry) for entry in excluded):
                reason = "excluded by tool.uv.workspace.exclude"
                found[relative] = WorkspaceMember(relative, skipped=reason)
            else:
                found[relative] = _member(root, candidate)
    return [found[key] for key in sorted(found)]


def _member(root: Path, directory: Path) -> WorkspaceMember:
    relative = directory.relative_to(root).as_posix()
    located = import_root(directory)
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


def _pyproject(directory: Path) -> dict[str, Any]:
    try:
        text = (directory / "pyproject.toml").read_text(encoding="utf-8")
        return tomllib.loads(text)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
