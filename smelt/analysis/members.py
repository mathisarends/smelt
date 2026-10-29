"""Conservative use evidence for module functions and direct class helpers."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Literal

from smelt.analysis.exports import resolve_dotted
from smelt.analysis.function_usage import (
    definition_bindings,
    explicit_exports,
    module_references,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.syntax import ModuleSyntax, SyntaxIndex

type Function = ast.FunctionDef | ast.AsyncFunctionDef
_REFLECTION_ARGUMENTS = 2


@dataclass(frozen=True)
class Member:
    module: str
    path: str
    owner: ast.ClassDef | None
    node: Function
    kind: Literal["function", "method", "staticmethod"]
    internal_references: tuple[int, ...]
    external_use: bool
    class_dependencies: bool = False
    occupied_names: frozenset[str] = frozenset()
    module_names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _AttributeUse:
    module: str
    line: int
    owner: ast.ClassDef | None
    internal: bool


class MemberUsageIndex:
    def __init__(self, syntax: SyntaxIndex) -> None:
        self._syntax = syntax

    @cached_property
    def members(self) -> list[Member]:
        syntax = self._syntax
        self._modules = [
            module
            for path in sorted(
                {s.path for s in syntax.files.sources.values()}
                | set(syntax.files.tests)
            )
            if (module := syntax.for_path(path)) is not None
        ]
        self._module_uses: dict[tuple[str, str | None], set[str]] = {}
        self._attributes: dict[str, list[_AttributeUse]] = {}
        self._inherited: set[tuple[str, str]] = set()
        for module in self._modules:
            for target in module_references(module, syntax):
                self._module_uses.setdefault(target, set()).add(module.module)
            visitor = _UseVisitor(module)
            visitor.visit(module.tree)
            for name, uses in visitor.attributes.items():
                self._attributes.setdefault(name, []).extend(uses)
            for node in ast.walk(module.tree):
                if isinstance(node, ast.ClassDef):
                    for base in node.bases:
                        qualified = _qualified(base, module)
                        base_target = (
                            resolve_dotted(qualified, syntax.namespace)
                            if qualified
                            else None
                        )
                        if base_target and base_target[1]:
                            self._inherited.add((base_target[0], base_target[1]))
        members: list[Member] = []
        for module in self._modules:
            if syntax.files.source_for_path(module.path) is not None:
                members.extend(self._members_in(module))
        return members

    def _members_in(self, module: ModuleSyntax) -> Iterator[Member]:
        definitions = definition_bindings(module.tree)
        occupied = self._module_names(module)
        exports = explicit_exports(module.tree)
        for node in module.tree.body:
            if definitions.get(getattr(node, "name", "")) != [node]:
                continue
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.decorator_list or exports is None or node.name in exports:
                    continue
                yield Member(
                    module.module,
                    module.path,
                    None,
                    node,
                    "function",
                    tuple(
                        sorted(
                            {
                                use.lineno
                                for use in ast.walk(module.tree)
                                if isinstance(use, ast.Name)
                                and isinstance(use.ctx, ast.Load)
                                and use.id == node.name
                            }
                        )
                    ),
                    self._function_external(module.module, node.name),
                    occupied_names=occupied,
                    module_names=occupied,
                )
            elif isinstance(node, ast.ClassDef) and self._plain_class(node, module):
                yield from self._methods_in(node, module)

    def _function_external(self, module: str, name: str) -> bool:
        parts = module.split(".")
        targets = [
            (module, name),
            *((".".join(parts[:end]), None) for end in range(1, len(parts) + 1)),
        ]
        return any(
            self._module_uses.get(target, set()) - {module} for target in targets
        )

    def _module_names(self, module: ModuleSyntax) -> frozenset[str]:
        namespace = self._syntax.namespace(module.module)
        return (
            namespace.defined | frozenset(namespace.imported)
            if namespace
            else frozenset()
        )

    def _plain_class(self, node: ast.ClassDef, module: ModuleSyntax) -> bool:
        return (
            not node.decorator_list
            and not node.keywords
            and all(
                _qualified(base, module) in {"object", "builtins.object"}
                for base in node.bases
            )
            and (module.module, node.name) not in self._inherited
        )

    def _methods_in(
        self, owner: ast.ClassDef, module: ModuleSyntax
    ) -> Iterator[Member]:
        definitions = definition_bindings(ast.Module(body=owner.body, type_ignores=[]))
        occupied = frozenset(definitions) | frozenset(
            name
            for name, uses in self._attributes.items()
            if any(use.owner is owner and use.internal for use in uses)
        )
        module_names = self._module_names(module)
        for node in owner.body:
            if not isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef
            ) or definitions.get(node.name) != [node]:
                continue
            decorators = [
                _qualified(decorator, module) for decorator in node.decorator_list
            ]
            if not decorators and (node.args.posonlyargs or node.args.args):
                kind: Literal["method", "staticmethod"] = "method"
            elif (
                len(decorators) == 1
                and decorators[0] in {"staticmethod", "builtins.staticmethod"}
                and not _shadows_staticmethod(owner, module)
            ):
                kind = "staticmethod"
            else:
                continue
            references: set[int] = set()
            external = False
            for use in self._attributes.get(node.name, ()):
                internal = (
                    use.module == module.module and use.owner is owner and use.internal
                )
                if internal:
                    references.add(use.line)
                else:
                    external = True
            yield Member(
                module.module,
                module.path,
                owner,
                node,
                kind,
                tuple(sorted(references)),
                external,
                _class_dependencies(node, owner),
                occupied,
                module_names,
            )


def _qualified(node: ast.AST, module: ModuleSyntax) -> str | None:
    if isinstance(node, ast.Name):
        return module.bindings.get(
            node.id,
            f"{module.module}.{node.id}"
            if any(
                isinstance(item, ast.ClassDef) and item.name == node.id
                for item in module.tree.body
            )
            else node.id,
        )
    if isinstance(node, ast.Attribute):
        parent = _qualified(node.value, module)
        return f"{parent}.{node.attr}" if parent else None
    if isinstance(node, ast.Subscript):
        return _qualified(node.value, module)
    return None


def _shadows_staticmethod(owner: ast.ClassDef, module: ModuleSyntax) -> bool:
    return any(
        (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store | ast.Del)
            and node.id == "staticmethod"
        )
        or (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and node.name == "staticmethod"
        )
        for root in [module.tree, owner]
        for node in ast.walk(root)
    )


def _class_dependencies(node: Function, owner: ast.ClassDef) -> bool:
    names = {"__class__", "super", owner.name}
    names.update(
        parameter.name
        for parameter in owner.type_params
        if isinstance(parameter, ast.TypeVar | ast.ParamSpec | ast.TypeVarTuple)
    )
    names.update(
        statement.name
        for statement in owner.body
        if isinstance(statement, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    )
    names.update(
        child.id
        for statement in owner.body
        if not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
        for child in ast.walk(statement)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
    )
    return any(
        isinstance(child, ast.Name)
        and isinstance(child.ctx, ast.Load)
        and child.id in names
        for child in ast.walk(node)
    )


class _UseVisitor(ast.NodeVisitor):
    def __init__(self, module: ModuleSyntax) -> None:
        self.module = module.module
        self.syntax = module
        self.owner: ast.ClassDef | None = None
        self.roots: set[str] = set()
        self.depth = 0
        self.attributes: dict[str, list[_AttributeUse]] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        owner, roots, depth = self.owner, self.roots, self.depth
        self.owner = node
        self.roots = set()
        self.depth = 0
        self.generic_visit(node)
        self.owner = owner
        self.roots = roots
        self.depth = depth

    def visit_FunctionDef(self, node: Function) -> None:
        previous = self.roots
        args = [*node.args.posonlyargs, *node.args.args]
        bound = set(definition_bindings(ast.Module(body=node.body, type_ignores=[])))
        arguments = [
            *args,
            *node.args.kwonlyargs,
            *(arg for arg in (node.args.vararg, node.args.kwarg) if arg),
        ]
        roots = {self.owner.name} if self.owner and not self.depth else set(previous)
        roots.difference_update(arg.arg for arg in arguments)
        roots.difference_update(bound)
        decorators = {
            _qualified(decorator, self.syntax) for decorator in node.decorator_list
        }
        if (
            self.owner
            and not self.depth
            and args
            and not decorators.intersection({"staticmethod", "builtins.staticmethod"})
            and args[0].arg not in bound
        ):
            roots.add(args[0].arg)
        self.roots = roots
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1
        self.roots = previous

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        previous = self.roots
        arguments = [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            *(arg for arg in (node.args.vararg, node.args.kwarg) if arg),
        ]
        self.roots = previous - {arg.arg for arg in arguments}
        self.generic_visit(node)
        self.roots = previous

    def visit_ListComp(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        previous = self.roots
        bound = {
            item.id
            for generator in node.generators
            for item in ast.walk(generator.target)
            if isinstance(item, ast.Name)
        }
        self.roots = previous - bound
        self.generic_visit(node)
        self.roots = previous

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self.visit_ListComp(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self.visit_ListComp(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self.visit_ListComp(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._record(node.attr, node.value, node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in {"getattr", "setattr", "hasattr", "delattr"}
            and len(node.args) >= _REFLECTION_ARGUMENTS
        ):
            name = node.args[1]
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                self._record(name.value, node.args[0], node.lineno)
        self.generic_visit(node)

    def _record(self, name: str, expression: ast.expr, line: int) -> None:
        self.attributes.setdefault(name, []).append(
            _AttributeUse(
                self.module,
                line,
                self.owner,
                isinstance(expression, ast.Name) and expression.id in self.roots,
            )
        )
