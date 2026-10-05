from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smelt.analysis.parsing import AstCache, resolve_relative

if TYPE_CHECKING:
    from smelt.analysis.files import FileIndex


@dataclass
class ModuleSyntax:
    module: str
    path: str
    tree: ast.Module
    is_package: bool
    # local name -> qualified name it was imported as
    bindings: dict[str, str] = field(default_factory=dict)


class SyntaxIndex:
    def __init__(self, files: FileIndex, asts: AstCache) -> None:
        self.files = files
        self._asts = asts
        self._by_path: dict[str, ModuleSyntax] = {}

    def for_path(self, path: str) -> ModuleSyntax | None:
        cached = self._by_path.get(path)
        if cached is not None:
            return cached
        source = self.files.source_for_path(path)
        if source is not None:
            name, is_package = source.module, source.is_package
        elif path in self.files.tests:
            name = path.removesuffix(".py").replace("/", ".")
            is_package = name.endswith(".__init__")
        else:
            return None
        syntax = ModuleSyntax(name, path, self._asts.parse(path), is_package)
        _collect_bindings(syntax)
        self._by_path[path] = syntax
        return syntax


def _collect_bindings(syntax: ModuleSyntax) -> None:
    for node in ast.walk(syntax.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    syntax.bindings[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    syntax.bindings.setdefault(top, top)
        elif isinstance(node, ast.ImportFrom):
            base = resolve_relative(
                syntax.module,
                is_package=syntax.is_package,
                level=node.level,
                target=node.module,
            )
            for alias in node.names:
                if alias.name != "*":
                    qualified = f"{base}.{alias.name}" if base else alias.name
                    syntax.bindings[alias.asname or alias.name] = qualified
