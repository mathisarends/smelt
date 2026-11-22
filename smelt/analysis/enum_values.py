"""Bounded recognition of direct member accesses on standard StrEnum classes."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import TypeAdapter

from smelt.analysis.exports import Namespace, resolve_dotted, resolve_export
from smelt.analysis.parsing import resolve_relative, tree_nodes

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import SourceFile
    from smelt.analysis.syntax import ModuleSyntax, SyntaxIndex

# A dotted name like ``enum.StrEnum``; other expressions never resolve.
type _Chain = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StrEnumValueAccess:
    module: str
    path: str
    enum: str
    member: str
    line: int
    column: int
    end_line: int | None
    end_column: int | None
    # the member expression, like ``ast.unparse`` of ``Kind.WHEEL``
    replacement: str


@dataclass(frozen=True, slots=True)
class _Assignment:
    """A class-body assignment that can define a member, in source order."""

    targets: tuple[str, ...]
    kind: Literal["string", "alias", "call"]
    # alias: the assigned name; call: the dotted callee
    reference: _Chain = ()


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A uniquely bound top-level class with one dotted base and a plain body."""

    name: str
    base: _Chain
    assignments: tuple[_Assignment, ...]


@dataclass(frozen=True, slots=True)
class _Access:
    receiver: _Chain
    member: str
    line: int
    column: int
    end_line: int | None
    end_column: int | None
    replacement: str


@dataclass(frozen=True, slots=True)
class _ModuleEnums:
    """The enum inputs of one module, a pure function of its path and content."""

    namespace: Namespace
    candidates: tuple[_Candidate, ...]
    accesses: tuple[_Access, ...]


_MODULE_ENUMS = TypeAdapter(_ModuleEnums)
_RESERVED = frozenset(
    {"value", "_ignore_", "__new__", "__init__", "__getattribute__", "__getattr__"}
)


def str_enum_value_accesses(syntax: SyntaxIndex) -> Iterator[StrEnumValueAccess]:
    enums = _Enums(syntax)
    for source, facts in enums.modules:
        namespace = facts.namespace
        for access in facts.accesses:
            enum = enums.resolve(access.receiver, namespace)
            if enum is not None and access.member in enums.members.get(enum, ()):
                yield StrEnumValueAccess(
                    source.module,
                    source.path,
                    enum,
                    access.member,
                    access.line,
                    access.column,
                    access.end_line,
                    access.end_column,
                    access.replacement,
                )


def _namespace(module: ModuleSyntax) -> Namespace:
    # A binding shadowed even in another scope is unknown. This intentionally
    # trades some recognition for avoiding guesses about Python name resolution.
    bound = _binding_counts(module)
    imported: dict[str, str] = {}
    if not bound["*"]:
        for node in module.tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    if bound[name] == 1:
                        imported[name] = alias.name if alias.asname else name
            elif isinstance(node, ast.ImportFrom):
                base = resolve_relative(
                    module.module,
                    is_package=module.is_package,
                    level=node.level,
                    target=node.module,
                )
                for alias in node.names:
                    name = alias.asname or alias.name
                    if bound[name] == 1 and name != "*":
                        imported[name] = f"{base}.{alias.name}"
    # Block fallback resolution to a submodule for uncertain/rebound names.
    return Namespace(module.module, imported, frozenset(bound.keys() - imported.keys()))


def _binding_counts(module: ModuleSyntax) -> Counter[str]:
    bound: Counter[str] = Counter()
    for node in module.nodes:
        bound.update(_bound_names(node))
        if isinstance(node, ast.Attribute | ast.Subscript) and isinstance(
            node.ctx, ast.Store | ast.Del
        ):
            root = node.value
            while isinstance(root, ast.Attribute | ast.Subscript):
                root = root.value
            if isinstance(root, ast.Name):
                bound[root.id] += 1
    return bound


class _Enums:
    def __init__(self, syntax: SyntaxIndex) -> None:
        self.modules = [
            (source, _module_enums(syntax, source))
            for source in sorted(
                syntax.files.sources.values(), key=lambda item: item.path
            )
        ]
        self.namespaces = {
            source.module: facts.namespace for source, facts in self.modules
        }
        for package in syntax.files.packages:
            self.namespaces.setdefault(package, Namespace(package))
        self.namespaces.setdefault(
            "enum", Namespace("enum", defined=frozenset({"StrEnum", "auto"}))
        )
        shadowed = "enum" in syntax.files.sources
        self.members: dict[str, frozenset[str]] = {}
        for source, facts in self.modules:
            for candidate in facts.candidates:
                self.members[f"{source.module}.{candidate.name}"] = (
                    frozenset()
                    if shadowed
                    else self._members(candidate, facts.namespace)
                )

    def resolve(self, chain: _Chain, namespace: Namespace) -> str | None:
        resolved = self._target(chain, namespace)
        if resolved is None or resolved[1] is None:
            return None
        return f"{resolved[0]}.{resolved[1]}"

    def _target(
        self, chain: _Chain, namespace: Namespace
    ) -> tuple[str, str | None] | None:
        if not chain:
            return None
        first, *attributes = chain
        qualified = namespace.imported.get(first, f"{namespace.module}.{first}")
        resolved = resolve_dotted(qualified, self.namespaces.get)
        for attribute in attributes:
            # Attributes of an already resolved class/member are unknown, rather
            # than another spelling of that class.
            if resolved is None or resolved[1] is not None:
                return None
            resolved = resolve_export(resolved[0], attribute, self.namespaces.get)
        return resolved

    def _members(self, candidate: _Candidate, namespace: Namespace) -> frozenset[str]:
        if self.resolve(candidate.base, namespace) != "enum.StrEnum":
            return frozenset()
        members: set[str] = set()
        for assignment in candidate.assignments:
            if (
                assignment.kind == "string"
                or (assignment.kind == "alias" and assignment.reference[0] in members)
                or (
                    assignment.kind == "call"
                    and self.resolve(assignment.reference, namespace) == "enum.auto"
                )
            ):
                members.update(assignment.targets)
        return frozenset(members)


def _module_enums(syntax: SyntaxIndex, source: SourceFile) -> _ModuleEnums:
    def build() -> _ModuleEnums:
        module = syntax.for_path(source.path)
        if module is None:
            raise LookupError(source.path)
        bound = _binding_counts(module)
        return _ModuleEnums(
            _namespace(module),
            () if bound["*"] else tuple(_candidates(module, bound)),
            tuple(_accesses(module)),
        )

    if syntax.cache is None:
        return build()
    return syntax.cache.get(
        "enum-module",
        [
            source.module,
            source.path,
            source.is_package,
            syntax.files.content_id(source.path),
        ],
        _MODULE_ENUMS,
        build=build,
    )


def _accesses(module: ModuleSyntax) -> Iterator[_Access]:
    for node in module.nodes:
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and node.attr == "value"
            and isinstance(node.value, ast.Attribute)
            and (receiver := _chain(node.value.value))
        ):
            yield _Access(
                receiver,
                node.value.attr,
                node.lineno,
                node.col_offset + 1,
                node.end_lineno,
                node.end_col_offset + 1 if node.end_col_offset is not None else None,
                ast.unparse(node.value),
            )


def _candidates(module: ModuleSyntax, bound: Counter[str]) -> Iterator[_Candidate]:
    for node in module.tree.body:
        if (
            isinstance(node, ast.ClassDef)
            and bound[node.name] == 1
            and len(node.bases) == 1
            and (base := _chain(node.bases[0]))
            and not node.keywords
            and not node.decorator_list
            and not _reserves_names(node)
        ):
            yield _Candidate(node.name, base, tuple(_assignments(node)))


def _reserves_names(node: ast.ClassDef) -> bool:
    return any(
        name in _RESERVED
        for child in node.body
        for binding in (
            tree_nodes(child)
            if isinstance(child, ast.Assign | ast.AnnAssign | ast.AugAssign)
            else (child,)
        )
        for name in _bound_names(binding)
    )


def _assignments(node: ast.ClassDef) -> Iterator[_Assignment]:
    value: ast.expr | None
    for child in node.body:
        if isinstance(child, ast.Assign):
            targets, value = child.targets, child.value
        elif isinstance(child, ast.AnnAssign):
            targets, value = [child.target], child.value
        else:
            continue
        names = tuple(
            target.id
            for target in targets
            if isinstance(target, ast.Name) and not target.id.startswith("_")
        )
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            yield _Assignment(names, "string")
        elif isinstance(value, ast.Name):
            yield _Assignment(names, "alias", (value.id,))
        elif (
            isinstance(value, ast.Call)
            and not value.args
            and not value.keywords
            and (callee := _chain(value.func))
        ):
            yield _Assignment(names, "call", callee)


def _chain(node: ast.expr) -> _Chain | None:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        base = _chain(node.value)
        return None if base is None else (*base, node.attr)
    return None


def _bound_names(node: ast.AST) -> tuple[str, ...]:
    match node:
        case (
            ast.Name(id=name, ctx=ast.Store() | ast.Del())
            | ast.arg(arg=name)
            | ast.FunctionDef(name=name)
            | ast.AsyncFunctionDef(name=name)
            | ast.ClassDef(name=name)
            | ast.TypeVar(name=name)
            | ast.ParamSpec(name=name)
            | ast.TypeVarTuple(name=name)
        ):
            return (name,)
        case ast.Import() | ast.ImportFrom():
            return tuple(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        case (
            ast.ExceptHandler(name=name)
            | ast.MatchAs(name=name)
            | ast.MatchStar(name=name)
            | ast.MatchMapping(rest=name)
        ):
            return (name,) if name else ()
        case ast.Global(names=names) | ast.Nonlocal(names=names):
            return tuple(names)
    return ()
