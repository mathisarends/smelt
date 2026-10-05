from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_MIRROR = "{path}/test_{module}.py"
ROOT_MIRROR = "{root}/{path}/test_{module}.py"


@dataclass(frozen=True)
class MirrorGuess:
    pattern: str
    mirrored: int  # test files that already sit at their mirrored path
    total: int


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
    return MirrorGuess(pattern, counts[pattern], total)


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
