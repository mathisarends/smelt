from __future__ import annotations

import ast
import re
import sys
import tomllib
from typing import TYPE_CHECKING

from smelt.analysis.errors import (
    AnalysisError as AnalysisError,  # noqa: PLC0414 - compatibility re-export
)
from smelt.analysis.python_version import PythonVersion
from smelt.config.source import SourceStore

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.analysis.cache import FactCache
    from smelt.analysis.files import FileIndex


class AstCache:
    def __init__(self, files: FileIndex, *, cache: FactCache | None = None) -> None:
        self.files = files
        self.cache = cache
        self._trees: dict[str, ast.Module] = {}

    def parse(self, path: str) -> ast.Module:
        tree = self._trees.get(path)
        if tree is None:
            source = self.files.read_text(path)
            try:
                tree = ast.parse(source, filename=path)
            except SyntaxError as exc:
                msg = f"{path}:{exc.lineno}: syntax error: {exc.msg}"
                raise AnalysisError(
                    msg + python_note(self.files.root, store=self.files.store)
                ) from exc
            self._trees[path] = tree
        return tree


class SyntaxVisitor(ast.NodeVisitor):
    """A read-only ``ast.NodeVisitor`` that visits children through ``child_nodes``."""

    def generic_visit(self, node: ast.AST) -> None:
        for child in child_nodes(node):
            self.visit(child)


def is_stdlib(module: str) -> bool:
    return module.split(".", maxsplit=1)[0] in sys.stdlib_module_names


_VERSION_BOUND = re.compile(r"(?:>=|~=|==)\s*3\.(\d+)")


def python_note(root: Path, *, store: SourceStore | None = None) -> str:
    """Explain a syntax error that may just be newer syntax than the running Python.

    ``ast`` only knows the grammar of the interpreter smelt runs on, so a 3.14
    project checked by smelt on 3.12 fails on ``except A, B:`` or t-strings.
    """
    running = sys.version_info[:2]
    note = f" (parsed by Python {running[0]}.{running[1]})"
    target = _target_python(root, store=store or SourceStore())
    if target is not None and target > running:
        version = f"{target.major}.{target.minor}"
        note += (
            f"; the project targets Python {version}, so run smelt on it, "
            f"e.g. `uvx -p {version} --from smelt-cli smelt check`"
        )
    return note


def type_checking_lines(tree: ast.Module) -> frozenset[int]:
    """Line numbers inside ``if TYPE_CHECKING:`` blocks."""
    lines: set[int] = set()
    for node in tree_nodes(tree):
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            for child in node.body:
                end = child.end_lineno or child.lineno
                lines.update(range(child.lineno, end + 1))
    return frozenset(lines)


def tree_nodes(tree: ast.AST) -> tuple[ast.AST, ...]:
    """Every node below ``tree`` in ``ast.walk`` order, walked once per root node.

    Analyses never mutate parsed trees, so every reader of one module, class or
    function shares a walk. Use this instead of ``ast.walk``: two dozen rules
    walk each module. Only code that rewrites the tree it walks needs ``ast.walk``.
    """
    # The tuple includes the root. Store it on the tree so the cycle can be
    # collected once the analysis drops the tree.
    nodes: tuple[ast.AST, ...] | None = tree.__dict__.get("_smelt_walk")
    if nodes is None:
        ordered = [tree]
        # The list is its own breadth-first queue, which is the order of ast.walk.
        for node in ordered:
            ordered.extend(child_nodes(node))
        nodes = tuple(ordered)
        tree.__dict__["_smelt_walk"] = nodes
    return nodes


def child_nodes(node: ast.AST) -> list[ast.AST]:
    """The children ``ast.iter_child_nodes`` yields, in its order, without its calls."""
    children: list[ast.AST] = []
    fields = node.__dict__
    for name in node._fields:
        value = fields.get(name)
        if isinstance(value, list):
            children += [item for item in value if isinstance(item, ast.AST)]
        elif isinstance(value, ast.AST):
            children.append(value)
    return children


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


def _target_python(root: Path, *, store: SourceStore) -> PythonVersion | None:
    found: list[PythonVersion] = []
    try:
        data = tomllib.loads(store.read_text(root / "pyproject.toml"))
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    requires = data.get("project", {}).get("requires-python", "")
    if isinstance(requires, str) and (match := _VERSION_BOUND.search(requires)):
        found.append(PythonVersion(3, int(match.group(1))))
    try:
        pinned = store.read_text(root / ".python-version").split()
    except OSError:
        pinned = []
    if pinned and (match := re.match(r"3\.(\d+)", pinned[0])):
        found.append(PythonVersion(3, int(match.group(1))))
    return max(found, default=None)


def _is_type_checking_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False
