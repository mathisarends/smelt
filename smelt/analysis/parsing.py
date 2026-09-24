from __future__ import annotations

import ast
import re
import sys
import tomllib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.analysis.files import FileIndex


class AnalysisError(Exception):
    """The project cannot be analyzed (missing packages, syntax errors, ...)."""


@dataclass
class AstCache:
    files: FileIndex
    _trees: dict[str, ast.Module] = field(default_factory=dict)

    def parse(self, path: str) -> ast.Module:
        tree = self._trees.get(path)
        if tree is None:
            source = self.files.read_text(path)
            try:
                tree = ast.parse(source, filename=path)
            except SyntaxError as exc:
                msg = f"{path}:{exc.lineno}: syntax error: {exc.msg}"
                raise AnalysisError(msg + python_note(self.files.root)) from exc
            self._trees[path] = tree
        return tree


_VERSION_BOUND = re.compile(r"(?:>=|~=|==)\s*3\.(\d+)")


def python_note(root: Path) -> str:
    """Explain a syntax error that may just be newer syntax than the running Python.

    ``ast`` only knows the grammar of the interpreter smelt runs on, so a 3.14
    project checked by smelt on 3.12 fails on ``except A, B:`` or t-strings.
    """
    running = sys.version_info[:2]
    note = f" (parsed by Python {running[0]}.{running[1]})"
    target = _target_python(root)
    if target is not None and target > running:
        version = f"{target[0]}.{target[1]}"
        note += (
            f"; the project targets Python {version}, so run smelt on it, "
            f"e.g. `uvx -p {version} smelt check`"
        )
    return note


def _target_python(root: Path) -> tuple[int, int] | None:
    found: list[tuple[int, int]] = []
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    requires = data.get("project", {}).get("requires-python", "")
    if isinstance(requires, str) and (match := _VERSION_BOUND.search(requires)):
        found.append((3, int(match.group(1))))
    try:
        pinned = (root / ".python-version").read_text(encoding="utf-8").split()
    except OSError:
        pinned = []
    if pinned and (match := re.match(r"3\.(\d+)", pinned[0])):
        found.append((3, int(match.group(1))))
    return max(found, default=None)


def type_checking_lines(tree: ast.Module) -> frozenset[int]:
    """Line numbers inside ``if TYPE_CHECKING:`` blocks."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            for child in node.body:
                end = child.end_lineno or child.lineno
                lines.update(range(child.lineno, end + 1))
    return frozenset(lines)


def _is_type_checking_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def resolve_relative(
    module: str, *, is_package: bool, level: int, target: str | None
) -> str:
    """Resolve ``from <level dots><target> import ...`` inside ``module``."""
    if level == 0:
        return target or ""
    parts = module.split(".")
    if not is_package:
        parts = parts[:-1]
    if level > 1:
        parts = parts[: len(parts) - (level - 1)]
    base = ".".join(parts)
    if target:
        return f"{base}.{target}" if base else target
    return base
