"""Find top-level packages on disk; shared by `smelt init` and config loading."""

from __future__ import annotations

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


def packages_in(base: Path) -> list[Path]:
    """Direct children of ``base`` that hold Python modules at their top level."""
    return [child for child in child_packages(base) if any(child.glob("*.py"))]
