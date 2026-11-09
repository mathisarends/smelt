"""Conservative module-qualified reads through stable lexical import bindings."""

from __future__ import annotations

import ast
from collections import Counter
from typing import TYPE_CHECKING

from smelt.analysis.parsing import resolve_relative

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.syntax import ModuleSyntax


def module_attributes(
    module: ModuleSyntax, ignored: frozenset[int]
) -> Iterator[tuple[ast.Attribute, str]]:
    visitor = _Attributes(module, ignored)
    visitor.visit(module.tree)
    yield from visitor.reads


class _Attributes(ast.NodeVisitor):
    def __init__(self, module: ModuleSyntax, ignored: frozenset[int]) -> None:
        self.module = module
        self.ignored = ignored
        self.env: dict[str, str] = {}
        self.closure: dict[str, str] = {}
        self.reads: list[tuple[ast.Attribute, str]] = []
        self.in_class = False

    def visit_Module(self, node: ast.Module) -> None:
        self._scope(node.body, {})

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load) and node.lineno not in self.ignored:
            qualified = _qualified(node.value, self.env)
            if qualified:
                self.reads.append((node, qualified))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for expression in [*node.decorator_list, node.args, *node.type_params]:
            self.visit(expression)
        if node.returns:
            self.visit(node.returns)
        outer = self.closure if self.in_class else self.env
        bindings = {arg.arg for arg in ast.walk(node.args) if isinstance(arg, ast.arg)}
        bindings.update(_type_parameters(node.type_params))
        self._scope(node.body, outer, bindings)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(expression)
        outer = self.closure if self.in_class else self.env
        self._scope(node.body, outer, _type_parameters(node.type_params), in_class=True)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        outer = self.closure if self.in_class else self.env
        bindings = {arg.arg for arg in ast.walk(node.args) if isinstance(arg, ast.arg)}
        self._scope([node.body], outer, bindings)

    def visit_ListComp(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        self.visit(node.generators[0].iter)
        previous = self.env
        outer = self.closure if self.in_class else self.env
        bound = {
            child.id
            for generator in node.generators
            for child in ast.walk(generator.target)
            if isinstance(child, ast.Name)
        }
        self.env = {name: value for name, value in outer.items() if name not in bound}
        for index, generator in enumerate(node.generators):
            if index:
                self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.env = previous

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self.visit_ListComp(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self.visit_ListComp(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self.visit_ListComp(node)

    def _scope(
        self,
        body: list[ast.stmt] | list[ast.expr],
        outer: dict[str, str],
        bound: set[str] | None = None,
        *,
        in_class: bool = False,
    ) -> None:
        previous = self.env, self.closure, self.in_class
        bindings = _Bindings(self.module, self.ignored)
        bindings.counts.update(bound or ())
        for child in body:
            bindings.visit(child)
        self.env = (
            {}
            if bindings.star
            else {
                name: value
                for name, value in outer.items()
                if name not in bindings.counts
            }
        )
        if not bindings.star:
            self.env.update(
                (name, value)
                for name, value in bindings.imports.items()
                if bindings.counts[name] == 1
            )
        self.closure = outer
        self.in_class = in_class
        for child in body:
            self.visit(child)
        self.env = previous[0]
        self.closure = previous[1]
        self.in_class = previous[2]


def _qualified(node: ast.expr, env: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.Attribute) and (base := _qualified(node.value, env)):
        return f"{base}.{node.attr}"
    return None


def _type_parameters(parameters: list[ast.type_param]) -> set[str]:
    return {
        param.name
        for param in parameters
        if isinstance(param, ast.TypeVar | ast.ParamSpec | ast.TypeVarTuple)
    }


class _Bindings(ast.NodeVisitor):
    def __init__(self, module: ModuleSyntax, ignored: frozenset[int]) -> None:
        self.module = module
        self.ignored = ignored
        self.counts: Counter[str] = Counter()
        self.imports: dict[str, str] = {}
        self.star = False

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store | ast.Del):
            self.counts[node.id] += 1

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            self.counts[name] += 1
            if node.lineno not in self.ignored:
                self.imports[name] = alias.name if alias.asname else name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = resolve_relative(
            self.module.module,
            is_package=self.module.is_package,
            level=node.level,
            target=node.module,
        )
        for alias in node.names:
            if alias.name == "*":
                self.star = True
            name = alias.asname or alias.name
            self.counts[name] += 1
            if node.lineno not in self.ignored:
                self.imports[name] = f"{base}.{alias.name}"

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.counts[node.name] += 1
        for expression in [*node.decorator_list, node.args]:
            self.visit(expression)
        if node.returns:
            self.visit(node.returns)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.counts[node.name] += 1
        for expression in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(expression)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.visit(node.iter)
        for condition in node.ifs:
            self.visit(condition)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.counts[node.name] += 1
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.counts[node.name] += 1
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.counts[node.name] += 1

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.counts[node.rest] += 1
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.counts.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.counts.update(node.names)
