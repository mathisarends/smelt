"""Find top-level packages on disk; shared by `smelt init` and config loading."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

from smelt.config.source import SourceStore

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


def has_python(directory: Path, *, store: SourceStore | None = None) -> bool:
    store = store or SourceStore()
    return (
        store.is_dir(directory)
        and directory.name.isidentifier()
        and directory.name not in IGNORED_DIRS
        and any(store.glob(directory, "**/*.py"))
    )


def child_packages(directory: Path, *, store: SourceStore | None = None) -> list[Path]:
    store = store or SourceStore()
    if not store.is_dir(directory):
        return []
    return sorted(
        (child for child in store.iterdir(directory) if has_python(child, store=store)),
        key=lambda p: p.name,
    )


def packages_in(
    base: Path, *, namespace: bool | None = None, store: SourceStore | None = None
) -> list[Path]:
    """Packages under an import root, including supported namespace packages.

    ``src/`` and build metadata permit a directory containing regular packages
    without Python files of its own. Loose script directories stay excluded.
    """
    store = store or SourceStore()
    declared, enabled = build_package_settings(
        base.parent if base.name == "src" else base, store=store
    )
    if namespace is None:
        namespace = base.name == "src" or enabled or bool(declared)
    found = [
        child
        for child in child_packages(base, store=store)
        if any(store.glob(child, "*.py"))
        or (
            namespace
            and any(store.is_file(c / "__init__.py") for c in store.iterdir(child))
        )
    ]
    return ([p for p in found if p.name in declared] or found) if declared else found


def build_package_settings(
    directory: Path, *, store: SourceStore | None = None
) -> tuple[set[str], bool]:
    """Top-level modules and namespace support declared by the uv build backend."""
    try:
        data = tomllib.loads(
            (store or SourceStore()).read_text(directory / "pyproject.toml")
        )
    except (OSError, tomllib.TOMLDecodeError):
        return set(), False
    backend = data.get("tool", {}).get("uv", {}).get("build-backend", {})
    raw = backend.get("module-name")
    names = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    return {name.split(".")[0] for name in names if isinstance(name, str)}, backend.get(
        "namespace"
    ) is True
