import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
                raise AnalysisError(msg) from exc
            self._trees[path] = tree
        return tree


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
