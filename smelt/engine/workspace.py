from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

IGNORED_DIRS = frozenset(
    {
        "tests",
        "test",
        "docs",
        "doc",
        "scripts",
        "examples",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "migrations",
        "venv",
        "env",
    }
)


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


def has_python(directory: Path) -> bool:
    return (
        directory.is_dir()
        and directory.name.isidentifier()
        and directory.name not in IGNORED_DIRS
        and any(directory.rglob("*.py"))
    )


def child_packages(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (child for child in directory.iterdir() if has_python(child)),
        key=lambda p: p.name,
    )


def packages_in(base: Path, *, namespace: bool = False) -> list[Path]:
    """Top-level packages in an import root.

    A directory counts when it holds Python files itself. With ``namespace`` a
    directory without any also counts if it contains regular packages: a PEP 420
    namespace like ``src/e2e_stack/server/__init__.py``. Callers allow that only
    where the directory must be importable, so ``deploy/`` with scripts stays out.
    """
    return [
        child
        for child in child_packages(base)
        if any(child.glob("*.py"))
        or (namespace and any((c / "__init__.py").is_file() for c in child.iterdir()))
    ]


def import_root(directory: Path) -> tuple[Path, list[Path], list[Path]] | None:
    """The source root of a project or member, its packages and their namespaces.

    ``src/`` wins when it holds a package; it and a project whose build backend
    declares ``namespace`` or a ``module-name`` may hold namespace packages.
    Otherwise the directory itself, with regular packages only.
    """
    declared, namespace = _build_backend(directory)
    for base in (directory / "src", directory):
        allowed = base.name == "src" or namespace or bool(declared)
        found = packages_in(base, namespace=allowed)
        if declared:
            found = [p for p in found if p.name in declared] or found
        if found:
            namespaces = [p for p in found if not any(p.glob("*.py"))]
            return base, found, namespaces
    return None


def _build_backend(directory: Path) -> tuple[set[str], bool]:
    """Top-level modules ``tool.uv.build-backend`` names, and whether it builds a namespace."""
    data = _pyproject(directory)
    backend = data.get("tool", {}).get("uv", {}).get("build-backend", {})
    raw = backend.get("module-name")
    names = [raw] if isinstance(raw, str) else _strings(raw)
    return {name.split(".")[0] for name in names}, backend.get("namespace") is True


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
