"""Bounded class ownership and receiver facts for private attribute accesses."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.bindings import binding_counts, qualified_name
from smelt.analysis.exports import MAX_HOPS, resolve_export

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import FileIndex
    from smelt.analysis.syntax import ModuleSyntax, SyntaxIndex

type _Function = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True, slots=True)
class PrivateAttributeAccess:
    node: ast.Attribute
    owner_module: str
    owner_class: str
    receiver_type: str
    operation: str


@dataclass(frozen=True, slots=True)
class _Value:
    name: str
    own_receiver: bool = False


@dataclass(frozen=True, slots=True)
class _Member:
    owner: str
    type_name: str | None


class PrivateAttributeIndex:
    def __init__(self, syntax: SyntaxIndex, files: FileIndex) -> None:
        self._syntax = syntax
        self._files = files
        self._bindings_cache: dict[str, dict[str, str]] = {}
        self._classes: dict[str, tuple[str, ast.ClassDef] | None] = {}
        self._members_cache: dict[str, dict[str, _Member]] = {}

    def accesses(self, module: ModuleSyntax) -> Iterator[PrivateAttributeAccess]:
        if self._syntax.attribute_candidates(module.path).private:
            yield from self._scope(module, module.tree.body, {}, module_scope=True)

    def _scope(
        self,
        syntax: ModuleSyntax,
        body: list[ast.stmt],
        env: dict[str, _Value],
        *,
        owner: ast.ClassDef | None = None,
        module_scope: bool = False,
    ) -> Iterator[PrivateAttributeAccess]:
        module = syntax.module
        candidates = self._syntax.attribute_candidates(syntax.path)
        writes = _writes(_scope_nodes(body))
        blocked = set(writes) | env.keys()
        if module_scope:
            blocked -= self._bindings(module).keys()
        for statement in body:
            has_access = candidates.may_access_private(statement)
            if isinstance(statement, ast.ClassDef):
                if has_access:
                    yield from self._scope(syntax, statement.body, {}, owner=statement)
                continue
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                if has_access:
                    parameters = self._function_env(module, statement, owner)
                    yield from self._scope(syntax, statement.body, parameters)
                continue
            if has_access:
                yield from self._statement_accesses(
                    module, statement, env, blocked=blocked
                )
            for target in _targets(statement):
                if not isinstance(target, ast.Name) or writes[target.id] != 1:
                    continue
                value = None
                if isinstance(statement, ast.AnnAssign):
                    name = self._annotation(module, statement.annotation)
                    value = _Value(name) if name else None
                elif isinstance(statement, ast.Assign):
                    value = self._value(module, statement.value, env, blocked=blocked)
                if value is not None:
                    env[target.id] = value

    def _function_env(
        self, module: str, function: _Function, owner: ast.ClassDef | None
    ) -> dict[str, _Value]:
        writes = _writes(_scope_nodes(function.body))
        parameters = self._parameters(module, function, writes)
        args = [*function.args.posonlyargs, *function.args.args]
        decorators = {
            qualified_name(node, self._bindings(module))
            for node in function.decorator_list
        }
        if (
            owner is not None
            and args
            and not writes[args[0].arg]
            and not decorators
            - {"builtins.property", "builtins.classmethod", "functools.cached_property"}
        ):
            parameters[args[0].arg] = _Value(
                f"{module}.{owner.name}", own_receiver=True
            )
        return parameters

    def _statement_accesses(
        self,
        module: str,
        statement: ast.stmt,
        env: dict[str, _Value],
        *,
        blocked: set[str],
    ) -> Iterator[PrivateAttributeAccess]:
        parents = {
            child: node
            for node in ast.walk(statement)
            for child in ast.iter_child_nodes(node)
        }
        for node in _scope_nodes([statement]):
            if (
                not isinstance(node, ast.Attribute)
                or not node.attr.startswith("_")
                or (node.attr.startswith("__") and node.attr.endswith("__"))
            ):
                continue
            receiver = self._value(module, node.value, env, blocked=blocked)
            if receiver is None or receiver.own_receiver:
                continue
            member = self._member(receiver.name, node.attr)
            if member is not None:
                owner_module, _, _ = member.owner.rpartition(".")
                yield PrivateAttributeAccess(
                    node,
                    owner_module,
                    member.owner,
                    receiver_type=receiver.name,
                    operation=_operation(node, parents.get(node)),
                )

    def _value(
        self,
        module: str,
        node: ast.expr,
        env: dict[str, _Value],
        *,
        blocked: set[str],
        depth: int = 0,
    ) -> _Value | None:
        if depth >= MAX_HOPS:
            return None
        if isinstance(node, ast.Name) and node.id in env:
            return env[node.id]
        candidate = node.func if isinstance(node, ast.Call) else node
        if not any(
            isinstance(part, ast.Name) and part.id in blocked
            for part in ast.walk(candidate)
        ):
            name = self._annotation(module, candidate)
            if name:
                return _Value(name)
        if isinstance(node, ast.Attribute):
            receiver = self._value(
                module, node.value, env, blocked=blocked, depth=depth + 1
            )
            member = self._member(receiver.name, node.attr) if receiver else None
            return _Value(member.type_name) if member and member.type_name else None
        return None

    def _member(self, name: str, attribute: str, *, depth: int = 0) -> _Member | None:
        if depth >= MAX_HOPS or (definition := self._class(name)) is None:
            return None
        module, owner = definition
        members = self._members(module, owner)
        if attribute in members:
            return members[attribute]
        # Do not guess a method resolution order across multiple or unknown bases.
        if len(owner.bases) == 1 and (base := self._annotation(module, owner.bases[0])):
            return self._member(base, attribute, depth=depth + 1)
        return None

    def _members(self, module: str, owner: ast.ClassDef) -> dict[str, _Member]:
        name = f"{module}.{owner.name}"
        if name in self._members_cache:
            return self._members_cache[name]
        members: dict[str, _Member] = {}
        self._members_cache[name] = members

        def record(attribute: str, type_name: str | None) -> None:
            previous = members.get(attribute)
            members[attribute] = _Member(
                name,
                type_name
                if previous is None or previous.type_name == type_name
                else None,
            )

        for statement in owner.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(
                statement.target, ast.Name
            ):
                record(
                    statement.target.id, self._annotation(module, statement.annotation)
                )
            elif isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        value = self._value(module, statement.value, {}, blocked=set())
                        record(target.id, value.name if value else None)
            elif isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                decorators = {
                    qualified_name(node, self._bindings(module))
                    for node in statement.decorator_list
                }
                returned = (
                    self._annotation(module, statement.returns)
                    if decorators & {"builtins.property", "functools.cached_property"}
                    else None
                )
                record(statement.name, returned)
                for attribute, value_type in self._method_fields(
                    module, statement, decorators
                ):
                    record(attribute, value_type)
        return members

    def _method_fields(
        self, module: str, method: _Function, decorators: set[str | None]
    ) -> Iterator[tuple[str, str | None]]:
        args = [*method.args.posonlyargs, *method.args.args]
        if not args or decorators - {
            "builtins.property",
            "builtins.classmethod",
            "functools.cached_property",
        }:
            return
        receiver = args[0].arg
        nodes = list(_scope_nodes(method.body))
        writes = _writes(nodes)
        if receiver in writes:
            return
        parameters = self._parameters(module, method, writes)
        for node in nodes:
            for target in _targets(node):
                if (
                    not isinstance(target, ast.Attribute)
                    or not isinstance(target.value, ast.Name)
                    or target.value.id != receiver
                ):
                    continue
                value_type = None
                if isinstance(node, ast.AnnAssign):
                    value_type = self._annotation(module, node.annotation)
                elif isinstance(node, ast.Assign) and method.name == "__init__":
                    value = self._value(
                        module,
                        node.value,
                        parameters,
                        blocked=set(writes) | parameters.keys(),
                    )
                    value_type = value.name if value else None
                yield target.attr, value_type

    def _parameters(
        self, module: str, function: _Function, writes: Counter[str]
    ) -> dict[str, _Value]:
        return {
            argument.arg: _Value(name)
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
            if not writes[argument.arg]
            and (name := self._annotation(module, argument.annotation))
        }

    def _annotation(self, module: str, node: ast.expr | None) -> str | None:
        match node:
            case ast.Constant(value=str() as annotation):
                try:
                    return self._annotation(
                        module, ast.parse(annotation, mode="eval").body
                    )
                except SyntaxError:
                    return None
            case ast.Subscript(value=origin, slice=argument) if qualified_name(
                origin, self._bindings(module)
            ) in {"typing.Annotated", "typing.Optional", "typing.ClassVar"}:
                return self._annotation(
                    module,
                    argument.elts[0] if isinstance(argument, ast.Tuple) else argument,
                )
            case (
                ast.BinOp(left=other, op=ast.BitOr(), right=ast.Constant(value=None))
                | ast.BinOp(left=ast.Constant(value=None), op=ast.BitOr(), right=other)
            ):
                return self._annotation(module, other)
        name = (
            qualified_name(node, self._bindings(module)) if node is not None else None
        )
        definition = self._class(name) if name else None
        return f"{definition[0]}.{definition[1].name}" if definition else None

    def _class(self, name: str) -> tuple[str, ast.ClassDef] | None:
        if name not in self._classes:
            self._classes[name] = None
            parts = name.split(".")
            prefix = ".".join(parts[:-1])
            if self._syntax.namespace(prefix) is None:
                return None
            resolved = resolve_export(prefix, parts[-1], self._syntax.namespace)
            if resolved is None:
                return None
            module, symbol = resolved
            if symbol is None:
                return None
            syntax = self._module(module)
            if (
                syntax is not None
                and self._bindings(module).get(symbol) == f"{module}.{symbol}"
            ):
                definitions = [
                    node
                    for node in syntax.tree.body
                    if isinstance(node, ast.ClassDef) and node.name == symbol
                ]
                if len(definitions) == 1:
                    node = definitions[0]
                    if all(
                        qualified_name(
                            decorator.func
                            if isinstance(decorator, ast.Call)
                            else decorator,
                            self._bindings(module),
                        )
                        == "dataclasses.dataclass"
                        for decorator in node.decorator_list
                    ):
                        self._classes[name] = module, node
        return self._classes[name]

    def _bindings(self, module: str) -> dict[str, str]:
        if module not in self._bindings_cache:
            syntax = self._module(module)
            namespace = self._syntax.namespace(module)
            if syntax is None or namespace is None:
                return {}
            counts = binding_counts(syntax.tree)
            self._bindings_cache[module] = {
                **{
                    name: target
                    for name, target in namespace.imported.items()
                    if counts[name] == 1
                },
                **{
                    name: f"{module}.{name}"
                    for name in namespace.defined
                    if counts[name] == 1
                },
                **{
                    name: f"builtins.{name}"
                    for name in ("property", "classmethod", "staticmethod")
                    if not counts[name]
                },
            }
        return self._bindings_cache[module]

    def _module(self, name: str) -> ModuleSyntax | None:
        source = self._files.sources.get(name)
        return self._syntax.for_path(source.path) if source is not None else None


def _scope_nodes(body: list[ast.stmt]) -> Iterator[ast.AST]:
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(
            node,
            ast.FunctionDef
            | ast.AsyncFunctionDef
            | ast.ClassDef
            | ast.Lambda
            | ast.ListComp
            | ast.SetComp
            | ast.DictComp
            | ast.GeneratorExp,
        ):
            pending.extend(ast.iter_child_nodes(node))


def _writes(nodes: Iterator[ast.AST] | list[ast.AST]) -> Counter[str]:
    result: Counter[str] = Counter()
    for node in nodes:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            result[node.id] += 1
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            result[node.name] += 1
        elif isinstance(node, ast.Import | ast.ImportFrom):
            result.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.Global | ast.Nonlocal):
            result.update(node.names)
        elif (
            isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar)
            and node.name
        ):
            result[node.name] += 1
        elif isinstance(node, ast.MatchMapping) and node.rest:
            result[node.rest] += 1
    return result


def _operation(node: ast.Attribute, parent: ast.AST | None) -> str:
    if isinstance(parent, ast.Call) and parent.func is node:
        return "call"
    if isinstance(parent, ast.AugAssign) and parent.target is node:
        return "update"
    if isinstance(node.ctx, ast.Store):
        return "write"
    return "delete" if isinstance(node.ctx, ast.Del) else "read"


def _targets(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.Assign | ast.Delete):
        return node.targets
    return [node.target] if isinstance(node, ast.AnnAssign | ast.AugAssign) else []
