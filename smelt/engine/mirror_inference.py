from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_MIRROR = "{path}/test_{module}.py"
ROOT_MIRROR = "{root}/{path}/test_{module}.py"


_IMPORTLIB = re.compile(r"import[-_]mode\W*importlib")
_PYTEST_CONFIGS = ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")


@dataclass(frozen=True, slots=True)
class MirrorGuess:
    pattern: str
    mirrored: int  # test files that already sit at their mirrored path
    total: int
    # Test files that share pytest's default import name.
    name_clashes: tuple[str, ...] = ()

    @property
    def draft_layout(self) -> str:
        return (
            "mirror"
            if self.mirrored == self.total and not self.name_clashes
            else "none"
        )


def infer_mirror(
    root: Path, test_roots: list[str], packages: list[Path]
) -> MirrorGuess | None:
    """The mirror pattern most existing tests follow, or None if none follows one."""
    modules = {package.name: _module_paths(package) for package in packages}
    counts = {DEFAULT_MIRROR: 0, ROOT_MIRROR: 0}
    total = 0
    for test_root in test_roots:
        directory = root / test_root
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("test_*.py")):
            total += 1
            parts = list(path.relative_to(directory).parent.parts)
            subject = path.stem.removeprefix("test_")
            if any(_mirrors(parts, subject, known) for known in modules.values()):
                counts[DEFAULT_MIRROR] += 1
            if parts and _mirrors(parts[1:], subject, modules.get(parts[0], set())):
                counts[ROOT_MIRROR] += 1
    pattern = max(counts, key=lambda key: counts[key])
    if counts[pattern] == 0:
        return None
    return MirrorGuess(
        pattern, counts[pattern], total, _clashing_test_roots(root, test_roots)
    )


def _clashing_test_roots(root: Path, test_roots: list[str]) -> tuple[str, ...]:
    """Actual clashing test files under pytest's default import mode."""
    found: dict[str, set[str]] = defaultdict(set)
    for test_root in test_roots:
        directory = root / test_root
        if not directory.is_dir():
            continue
        configs = [
            base / name
            for base in dict.fromkeys((root, directory.parent))
            for name in _PYTEST_CONFIGS
        ]
        if any(
            config.is_file()
            and _IMPORTLIB.search(config.read_text(encoding="utf-8", errors="replace"))
            for config in configs
        ):
            continue
        for path in directory.rglob("test_*.py"):
            names = [path.stem]
            for parent in path.parents:
                if (
                    not parent.is_relative_to(root)
                    or not (parent / "__init__.py").is_file()
                ):
                    break
                names.append(parent.name)
            found[".".join(reversed(names))].add(path.relative_to(root).as_posix())
    return tuple(
        sorted(path for paths in found.values() if len(paths) > 1 for path in paths)
    )


def _module_paths(package: Path) -> set[str]:
    """``billing/invoice`` and ``billing/__init__`` style paths inside a package."""
    return {
        path.relative_to(package).with_suffix("").as_posix()
        for path in package.rglob("*.py")
    }


def _mirrors(directories: list[str], subject: str, known: set[str]) -> bool:
    if "/".join([*directories, subject]) in known:
        return True
    return (
        bool(directories)
        and directories[-1] == subject
        and ("/".join([*directories, "__init__"]) in known)
    )
