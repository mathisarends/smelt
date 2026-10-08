"""Find top-level packages on disk; shared by `smelt init` and config loading."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

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


def packages_in(base: Path, *, namespace: bool | None = None) -> list[Path]:
    """Packages under an import root, including supported namespace packages.

    ``src/`` and build metadata permit a directory containing regular packages
    without Python files of its own. Loose script directories stay excluded.
    """
    declared, enabled = build_package_settings(
        base.parent if base.name == "src" else base
    )
    if namespace is None:
        namespace = base.name == "src" or enabled or bool(declared)
    found = [
        child
        for child in child_packages(base)
        if any(child.glob("*.py"))
        or (namespace and any((c / "__init__.py").is_file() for c in child.iterdir()))
    ]
    return ([p for p in found if p.name in declared] or found) if declared else found


def build_package_settings(directory: Path) -> tuple[set[str], bool]:
    """Top-level modules and namespace support declared by the uv build backend."""
    try:
        data = tomllib.loads((directory / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set(), False
    backend = data.get("tool", {}).get("uv", {}).get("build-backend", {})
    raw = backend.get("module-name")
    names = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    return {name.split(".")[0] for name in names if isinstance(name, str)}, backend.get(
        "namespace"
    ) is True
