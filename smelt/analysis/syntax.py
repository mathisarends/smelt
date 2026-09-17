from __future__ import annotations

import ast
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING

from smelt.analysis.parsing import AstCache, resolve_relative, type_checking_lines

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import FileIndex

_MAX_REEXPORT_DEPTH = 10


@dataclass(frozen=True, slots=True)
class ClassInfo:
    name: str
    qualname: str
    module: str
    path: str
    node: ast.ClassDef
    bases: tuple[str | None, ...]  # resolved qualified names, None if unresolvable

    @property
    def line(self) -> int:
        return self.node.lineno

    @property
    def column(self) -> int:
        return self.node.col_offset + 1

    @property
    def name_span(self) -> tuple[int, int]:
        """1-based columns of the class name in ``class Name(...)``."""
        start = self.node.col_offset + len("class ") + 1
        return start, start + len(self.name)


@dataclass
class ModuleSyntax:
    module: str
    path: str
    tree: ast.Module
    is_package: bool
    is_test: bool
    bindings: dict[str, str] = field(default_factory=dict)
    definitions: dict[str, ast.stmt] = field(default_factory=dict)
    classes: list[ClassInfo] = field(default_factory=list)

    @cached_property
    def type_checking_lines(self) -> frozenset[int]:
        return type_checking_lines(self.tree)

    def resolve(self, expr: ast.expr | None) -> str | None:
        """Qualified name of a ``Name``/``Attribute`` chain, resolved through imports."""
        match expr:
            case ast.Name(id=name):
                return self._resolve_name(name)
            case ast.Attribute(value=value, attr=attr):
                base = self.resolve(value)
                return f"{base}.{attr}" if base else None
            case ast.Subscript(value=value):
                return self.resolve(value)
            case ast.Constant(value=str() as text):
                return self.resolve(_parse_expression(text))
            case _:
                return None

    def _resolve_name(self, name: str) -> str | None:
        if name in self.bindings:
            return self.bindings[name]
        if name in self.definitions:
            return f"{self.module}.{name}"
        return None

    def walk(self) -> Iterator[ast.AST]:
        return ast.walk(self.tree)


class SyntaxIndex:
    def __init__(self, files: FileIndex, asts: AstCache) -> None:
        self.files = files
        self._asts = asts
        self._by_path: dict[str, ModuleSyntax] = {}

    def module(self, module: str) -> ModuleSyntax | None:
        source = self.files.sources.get(module)
        return self.for_path(source.path) if source else None

    def for_path(self, path: str) -> ModuleSyntax | None:
        cached = self._by_path.get(path)
        if cached is not None:
            return cached
        source = self.files.source_for_path(path)
        if source is not None:
            name, is_package, is_test = source.module, source.is_package, False
        elif path in self.files.tests:
            name = path.removesuffix(".py").replace("/", ".")
            is_package, is_test = name.endswith(".__init__"), True
        else:
            return None
        syntax = _build_module_syntax(
            name, path, self._asts.parse(path), is_package, is_test
        )
        self._by_path[path] = syntax
        return syntax

    def sources(self) -> Iterator[ModuleSyntax]:
        for module in sorted(self.files.sources):
            syntax = self.module(module)
            if syntax is not None:
                yield syntax

    def tests(self) -> Iterator[ModuleSyntax]:
        for path in sorted(self.files.tests):
            syntax = self.for_path(path)
            if syntax is not None:
                yield syntax

    @cached_property
    def classes(self) -> dict[str, ClassInfo]:
        return {
            info.qualname: info for syntax in self.sources() for info in syntax.classes
        }

    def canonical(self, qualname: str | None, _depth: int = 0) -> str | None:
        """Follow re-exports (``from .impl import Foo`` in ``__init__``) to the definition."""
        if (
            qualname is None
            or _depth > _MAX_REEXPORT_DEPTH
            or qualname in self.files.sources
        ):
            return qualname
        parts = qualname.split(".")
        for split in range(len(parts) - 1, 0, -1):
            module = ".".join(parts[:split])
            syntax = self.module(module)
            if syntax is None:
                continue
            head, rest = parts[split], parts[split + 1 :]
            if head in syntax.definitions:
                return qualname
            target = syntax.bindings.get(head)
            if target is None or target == qualname:
                return qualname
            return self.canonical(".".join([target, *rest]), _depth + 1)
        return qualname

    def resolve(self, syntax: ModuleSyntax, expr: ast.expr | None) -> str | None:
        return self.canonical(syntax.resolve(expr))

    def class_info(self, qualname: str | None) -> ClassInfo | None:
        canonical = self.canonical(qualname)
        return self.classes.get(canonical) if canonical else None

    def ancestors(self, info: ClassInfo) -> list[str]:
        """Transitive base names; first-party bases are followed, external ones are leaves."""
        seen: list[str] = []
        stack = [base for base in reversed(info.bases) if base]
        while stack:
            base = self.canonical(stack.pop())
            if base is None or base in seen:
                continue
            seen.append(base)
            parent = self.classes.get(base)
            if parent is not None:
                stack.extend(b for b in reversed(parent.bases) if b)
        return seen

    def is_first_party(self, qualname: str) -> bool:
        return (
            qualname.split(".", maxsplit=1)[0]
            in self.files.config.project.root_packages
        )


def _build_module_syntax(
    module: str, path: str, tree: ast.Module, is_package: bool, is_test: bool
) -> ModuleSyntax:
    syntax = ModuleSyntax(module, path, tree, is_package, is_test)
    _collect_bindings(syntax)
    for statement in tree.body:
        for name in _defined_names(statement):
            syntax.definitions[name] = statement
    for statement in tree.body:
        if isinstance(statement, ast.ClassDef):
            syntax.classes.append(
                ClassInfo(
                    name=statement.name,
                    qualname=f"{module}.{statement.name}",
                    module=module,
                    path=path,
                    node=statement,
                    bases=tuple(syntax.resolve(base) for base in statement.bases),
                )
            )
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


def _defined_names(statement: ast.stmt) -> list[str]:
    match statement:
        case ast.ClassDef(name=name) | ast.FunctionDef(name=name):
            return [name]
        case ast.AsyncFunctionDef(name=name):
            return [name]
        case ast.Assign(targets=targets):
            return [t.id for t in targets if isinstance(t, ast.Name)]
        case (
            ast.AnnAssign(target=ast.Name(id=name))
            | ast.TypeAlias(name=ast.Name(id=name))
        ):
            return [name]
        case _:
            return []


def _parse_expression(text: str) -> ast.expr | None:
    try:
        return ast.parse(text.strip(), mode="eval").body
    except SyntaxError:
        return None
