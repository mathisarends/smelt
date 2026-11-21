"""Bounded class ownership and receiver facts for private attribute accesses."""

from __future__ import annotations

import ast
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, NamedTuple

from pydantic import TypeAdapter

from smelt.analysis.bindings import binding_counts, qualified_name
from smelt.analysis.exports import MAX_HOPS, resolve_export

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.exports import Namespace
    from smelt.analysis.files import FileIndex, SourceFile
    from smelt.analysis.syntax import ModuleSyntax, SyntaxIndex

type _Function = ast.FunctionDef | ast.AsyncFunctionDef

_RECEIVER_DECORATORS = frozenset(
    {"builtins.property", "builtins.classmethod", "functools.cached_property"}
)


@dataclass(frozen=True, slots=True)
class PrivateAttributeAccess:
    attribute: str
    line: int
    column: int
    end_line: int | None
    end_column: int
    owner_module: str
    owner_class: str
    receiver_type: str
    operation: str


@dataclass(frozen=True, slots=True)
class _FieldFacts:
    """A receiver attribute a method assigns, with the source of its type evidence."""

    attribute: str
    annotation: str | None = None
    # only recorded for ``__init__``, the one method whose values type fields
    value: str | None = None


class _Parameter(NamedTuple):
    name: str
    annotation: str


@dataclass(frozen=True, slots=True)
class _MethodFacts:
    name: str
    # qualified decorator names; unknown decorators are None
    decorators: tuple[str | None, ...]
    returns: str | None
    fields: tuple[_FieldFacts, ...] = ()
    # unwritten parameters and the source of their annotations
    parameters: tuple[_Parameter, ...] = ()
    # names written in the method that its field values mention
    blocked: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _BodyFacts:
    """One class-body statement that can define members, in source order."""

    names: tuple[str, ...] = ()
    annotation: str | None = None
    value: str | None = None
    method: _MethodFacts | None = None


@dataclass(frozen=True, slots=True)
class _ClassFacts:
    dataclass_only: bool
    # the source of the only base, if the class has exactly one
    base: str | None
    body: tuple[_BodyFacts, ...]


@dataclass(frozen=True, slots=True)
class _ModuleFacts:
    """Ownership inputs of one module: a function of its path and contents only.

    Expressions are kept as source so that edits elsewhere in a class, such
    as most method bodies, leave the facts and every dependent decision intact.
    """

    bindings: dict[str, str]
    # uniquely defined top-level classes
    classes: dict[str, _ClassFacts]


_FACTS = TypeAdapter(_ModuleFacts)
_ACCESSES = TypeAdapter(tuple[PrivateAttributeAccess, ...])


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
        self._facts: dict[str, _ModuleFacts | None] = {}
        self._classes: dict[str, tuple[str, _ClassFacts] | None] = {}
        self._members_cache: dict[str, dict[str, _Member]] = {}

    def accesses(self, source: SourceFile) -> tuple[PrivateAttributeAccess, ...]:
        """Accesses in ``source``, reused while it and every module's facts hold."""
        if not self._syntax.attribute_candidates(source.path).private:
            return ()

        def build() -> tuple[PrivateAttributeAccess, ...]:
            module = self._syntax.for_path(source.path)
            if module is None:
                raise LookupError(source.path)
            return tuple(self._scope(module, module.tree.body, {}, module_scope=True))

        cache = self._syntax.cache
        if cache is None:
            return build()
        return cache.get(
            "private-accesses",
            [
                source.module,
                source.path,
                source.is_package,
                self._files.content_id(source.path),
                self._project,
            ],
            _ACCESSES,
            build=build,
        )

    @cached_property
    def _project(self) -> str:
        """Every input that resolution may read beyond the accessing module."""
        digest = hashlib.sha256()
        digest.update(json.dumps(sorted(self._files.packages)).encode())
        for source in sorted(self._files.sources.values(), key=lambda item: item.path):
            namespace = self._syntax.namespace(source.module)
            digest.update(
                json.dumps(
                    [
                        source.module,
                        source.path,
                        source.is_package,
                        _namespace_inputs(namespace),
                    ]
                ).encode()
            )
            facts = self._module_facts(source.module)
            digest.update(b"" if facts is None else _FACTS.dump_json(facts))
        return digest.hexdigest()

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
        parents: dict[ast.AST, ast.AST] | None = None
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
                if parents is None:
                    parents = {
                        child: node
                        for node in ast.walk(statement)
                        for child in ast.iter_child_nodes(node)
                    }
                owner_module, _, _ = member.owner.rpartition(".")
                yield PrivateAttributeAccess(
                    node.attr,
                    node.lineno,
                    node.col_offset + 1,
                    node.end_lineno,
                    (node.end_col_offset or 0) + 1,
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
        qualified, owner = definition
        module = qualified.rpartition(".")[0]
        members = self._members(qualified, owner)
        if attribute in members:
            return members[attribute]
        # Do not guess a method resolution order across multiple or unknown bases.
        if owner.base is not None and (
            base := self._annotation(module, _expression(owner.base))
        ):
            return self._member(base, attribute, depth=depth + 1)
        return None

    def _members(self, name: str, owner: _ClassFacts) -> dict[str, _Member]:
        module = name.rpartition(".")[0]
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
            if statement.method is not None:
                method = statement.method
                returned = (
                    self._annotation(module, _expression(method.returns))
                    if method.returns is not None
                    and {"builtins.property", "functools.cached_property"}
                    & set(method.decorators)
                    else None
                )
                record(method.name, returned)
                for attribute, value_type in self._method_fields(module, method):
                    record(attribute, value_type)
            elif statement.annotation is not None:
                record(
                    statement.names[0],
                    self._annotation(module, _expression(statement.annotation)),
                )
            elif statement.value is not None:
                for target in statement.names:
                    value = self._value(
                        module, _expression(statement.value), {}, blocked=set()
                    )
                    record(target, value.name if value else None)
        return members

    def _method_fields(
        self, module: str, method: _MethodFacts
    ) -> Iterator[tuple[str, str | None]]:
        parameters = {
            argument: _Value(name)
            for argument, annotation in method.parameters
            if (name := self._annotation(module, _expression(annotation)))
        }
        blocked = set(method.blocked) | parameters.keys()
        for field in method.fields:
            value_type = None
            if field.annotation is not None:
                value_type = self._annotation(module, _expression(field.annotation))
            elif field.value is not None:
                value = self._value(
                    module, _expression(field.value), parameters, blocked=blocked
                )
                value_type = value.name if value else None
            yield field.attribute, value_type

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
        return definition[0] if definition else None

    def _class(self, name: str) -> tuple[str, _ClassFacts] | None:
        """The qualified name and facts of the class ``name`` resolves to."""
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
            facts = self._module_facts(module)
            if (
                facts is not None
                and facts.bindings.get(symbol) == f"{module}.{symbol}"
                and (owner := facts.classes.get(symbol)) is not None
                and owner.dataclass_only
            ):
                self._classes[name] = f"{module}.{symbol}", owner
        return self._classes[name]

    def _bindings(self, module: str) -> dict[str, str]:
        facts = self._module_facts(module)
        return {} if facts is None else facts.bindings

    def _module_facts(self, module: str) -> _ModuleFacts | None:
        if module not in self._facts:
            source = self._files.sources.get(module)
            self._facts[module] = None if source is None else self._load(source)
        return self._facts[module]

    def _load(self, source: SourceFile) -> _ModuleFacts:
        def build() -> _ModuleFacts:
            syntax = self._syntax.for_path(source.path)
            namespace = self._syntax.namespace(source.module)
            if syntax is None or namespace is None:
                raise LookupError(source.path)
            return _module_facts(syntax, namespace)

        cache = self._syntax.cache
        if cache is None:
            return build()
        return cache.get(
            "private-ownership",
            [
                source.module,
                source.path,
                source.is_package,
                self._files.content_id(source.path),
            ],
            _FACTS,
            build=build,
        )


def _module_facts(syntax: ModuleSyntax, namespace: Namespace) -> _ModuleFacts:
    module = syntax.module
    counts = binding_counts(syntax.tree)
    bindings = {
        **{
            name: target
            for name, target in namespace.imported.items()
            if counts[name] == 1
        },
        **{name: f"{module}.{name}" for name in namespace.defined if counts[name] == 1},
        **{
            name: f"builtins.{name}"
            for name in ("property", "classmethod", "staticmethod")
            if not counts[name]
        },
    }
    classes = [node for node in syntax.tree.body if isinstance(node, ast.ClassDef)]
    defined = Counter(node.name for node in classes)
    return _ModuleFacts(
        bindings,
        {
            node.name: _class_facts(node, bindings)
            for node in classes
            if defined[node.name] == 1
        },
    )


def _class_facts(node: ast.ClassDef, bindings: dict[str, str]) -> _ClassFacts:
    body: list[_BodyFacts] = []
    for statement in node.body:
        if isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            body.append(
                _BodyFacts(
                    (statement.target.id,), annotation=ast.unparse(statement.annotation)
                )
            )
        elif isinstance(statement, ast.Assign):
            names = tuple(
                target.id
                for target in statement.targets
                if isinstance(target, ast.Name)
            )
            if names:
                body.append(_BodyFacts(names, value=ast.unparse(statement.value)))
        elif isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            body.append(_BodyFacts(method=_method_facts(statement, bindings)))
    return _ClassFacts(
        dataclass_only=all(
            qualified_name(
                decorator.func if isinstance(decorator, ast.Call) else decorator,
                bindings,
            )
            == "dataclasses.dataclass"
            for decorator in node.decorator_list
        ),
        base=ast.unparse(node.bases[0]) if len(node.bases) == 1 else None,
        body=tuple(body),
    )


def _method_facts(method: _Function, bindings: dict[str, str]) -> _MethodFacts:
    decorators = tuple(qualified_name(node, bindings) for node in method.decorator_list)
    returns = ast.unparse(method.returns) if method.returns is not None else None
    args = [*method.args.posonlyargs, *method.args.args]
    if not args or set(decorators) - _RECEIVER_DECORATORS:
        return _MethodFacts(method.name, decorators, returns)
    receiver = args[0].arg
    nodes = list(_scope_nodes(method.body))
    writes = _writes(nodes)
    if receiver in writes:
        return _MethodFacts(method.name, decorators, returns)
    fields: list[_FieldFacts] = []
    mentioned: set[str] = set()
    for node in nodes:
        for target in _targets(node):
            if (
                not isinstance(target, ast.Attribute)
                or not isinstance(target.value, ast.Name)
                or target.value.id != receiver
            ):
                continue
            if isinstance(node, ast.AnnAssign):
                fields.append(
                    _FieldFacts(target.attr, annotation=ast.unparse(node.annotation))
                )
            elif isinstance(node, ast.Assign) and method.name == "__init__":
                fields.append(_FieldFacts(target.attr, value=ast.unparse(node.value)))
                mentioned.update(
                    part.id
                    for part in ast.walk(node.value)
                    if isinstance(part, ast.Name)
                )
            else:
                fields.append(_FieldFacts(target.attr))
    return _MethodFacts(
        method.name,
        decorators,
        returns,
        tuple(fields),
        tuple(
            _Parameter(argument.arg, ast.unparse(argument.annotation))
            for argument in (*args, *method.args.kwonlyargs)
            if not writes[argument.arg] and argument.annotation is not None
        ),
        tuple(sorted(mentioned & writes.keys())),
    )


def _expression(source: str) -> ast.expr:
    # Parentheses admit every expression ``ast.unparse`` can produce, like yield.
    return ast.parse(f"({source})", mode="eval").body


def _namespace_inputs(namespace: Namespace | None) -> object:
    if namespace is None:
        return None
    return [
        sorted(namespace.imported.items()),
        sorted(namespace.defined),
        sorted(namespace.references.items()),
    ]


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
