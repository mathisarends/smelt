"""Conservative use evidence for module functions and direct class helpers."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field, replace
from functools import cached_property, partial
from typing import TYPE_CHECKING, Literal

from pydantic import TypeAdapter

from smelt.analysis.exports import Namespace, resolve_dotted, resolve_export
from smelt.analysis.function_usage import (
    definition_bindings,
    explicit_exports,
    reference_names,
)
from smelt.analysis.member_state import (
    MEMBER_STATE,
    MemberIndexState,
    MemberSummary,
    update_members,
)
from smelt.analysis.retained import current

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.syntax import ModuleSyntax, SyntaxIndex
    from smelt.analysis.test_syntax import TestSyntaxIndex

type Function = ast.FunctionDef | ast.AsyncFunctionDef
_REFLECTION_ARGUMENTS = 2


@dataclass(frozen=True)
class Definition:
    name: str
    lineno: int
    col_offset: int

    @classmethod
    def of(cls, node: Function | ast.ClassDef) -> Definition:
        return cls(node.name, node.lineno, node.col_offset)


@dataclass(frozen=True)
class Member:
    module: str
    path: str
    owner: Definition | None
    node: Definition
    kind: Literal["function", "method", "staticmethod"]
    internal_references: tuple[int, ...]
    external_use: bool
    production_external_use: bool = False
    class_dependencies: bool = False
    occupied_names: frozenset[str] = frozenset()
    module_names: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _AttributeUse:
    module: str
    path: str
    line: int
    owner: ast.ClassDef | None
    internal: bool
    definition: ast.Attribute | None = None


@dataclass(frozen=True)
class AttributeMember:
    module: str
    path: str
    owner: Definition
    node: Definition
    name: str
    kind: Literal["instance-attribute", "class-attribute"]
    internal_references: tuple[int, ...]
    occupied_names: frozenset[str]


@dataclass
class _UsageEvidence:
    module_uses: dict[tuple[str, str | None], set[str]]
    production_uses: dict[tuple[str, str | None], set[str]]
    attributes: dict[str, list[tuple[str, int, int | None, bool, bool]]]
    inherited: set[tuple[str, str | None]]


@dataclass
class _FileFacts:
    module: str
    path: str
    production: bool
    candidates: list[Member]
    references: tuple[list[str], list[str], list[str]]
    attributes: dict[str, list[tuple[int, int | None, bool]]]
    bases: list[str]
    attribute_candidates: list[AttributeMember] = field(default_factory=list)


_FACTS = TypeAdapter(_FileFacts)


@dataclass
class _ResolvedReferences:
    targets: list[tuple[str, str | None]]
    inherited: list[tuple[str, str | None]]
    dependencies: dict[str, tuple[str, str, bool] | None]


_REFERENCES = TypeAdapter(_ResolvedReferences)
_MEMBERS = TypeAdapter(list[Member])
type MemberState = tuple[bool, bool, tuple[int, ...], bool]


class MemberUsageIndex:
    def __init__(self, syntax: SyntaxIndex) -> None:
        self._syntax = syntax
        self._decision_keys: dict[str, str] = {}

    @cached_property
    def _local_state(self) -> MemberIndexState:
        syntax = self._syntax
        cache = syntax.cache
        if cache is None:
            raise ValueError("member dependency state requires a fact cache")
        key = cache.key(
            "member-state",
            [str(syntax.files.root), syntax.files.config.model_dump(mode="json")],
        )
        retained = current()
        state_key = str(cache.directory), key
        previous = retained.take(state_key) if retained is not None else None
        previous = previous or cache.read(key, MEMBER_STATE) or MemberIndexState()
        identities = {
            path: self._fact_key(path)
            for path in sorted(set(syntax.files.by_path) | syntax.files.tests.keys())
        }
        namespaces = {
            name: self._namespace_identity(name)
            for name in syntax.files.sources.keys() | syntax.files.packages.keys()
        }

        def decision(summary: MemberSummary, totals: dict[str, int]) -> str:
            return cache.key(
                "member-decision",
                [summary.identity, [totals.get(query, 0) for query in summary.queries]],
            )

        state = update_members(
            previous, identities, namespaces, self._summary, decision
        )
        cache.write(key, state, MEMBER_STATE)
        if retained is not None:
            retained.put(state_key, state, size=len(MEMBER_STATE.dump_json(state)))
        return state

    def local_paths(self) -> Iterator[str]:
        """Yield decisions without deserializing unchanged definition objects."""
        for path, summary in self._local_state.files.items():
            if summary.has_members:
                yield path

    def members_for_path(self, path: str) -> list[Member]:
        cache = self._syntax.cache
        if cache is None:
            return self.member_files.get(path, [])
        state = self._local_state
        key = state.files[path].decision

        def build() -> list[Member]:
            file = self._facts(path)
            states: list[MemberState] = []
            for candidate in file.candidates:
                owner = candidate.owner
                if owner is None:
                    states.append(
                        (
                            self._counted_external(file.module, candidate.node.name),
                            self._counted_external(
                                file.module, candidate.node.name, production=True
                            ),
                            candidate.internal_references,
                            False,
                        )
                    )
                    continue
                local = [
                    line
                    for line, line_owner, internal in file.attributes.get(
                        candidate.node.name, []
                    )
                    if line_owner == owner.lineno and internal
                ]
                name = candidate.node.name
                states.append(
                    (
                        state.totals.get(f"a:{name}", 0) > len(local),
                        state.totals.get(f"p:{name}", 0) > len(local),
                        tuple(sorted(set(local))),
                        bool(state.totals.get(f"i:{file.module}:{owner.name}", 0)),
                    )
                )
            return self._resolve_file(file, states)

        return cache.get("member-values", key, _MEMBERS, build)

    def _counted_external(
        self, module: str, name: str, *, production: bool = False
    ) -> bool:
        totals = self._local_state.totals
        prefix = "pf" if production else "f"
        return any(
            totals.get(f"{prefix}:{target}", 0)
            > totals.get(f"{prefix}:{target}:{module}", 0)
            for target in _function_targets(module, name)
        )

    def _summary(self, path: str) -> MemberSummary:
        file = self._facts(path)
        references = self._references(file)
        contributions: dict[str, int] = {}
        for module, name in set(references.targets):
            target = f"{module}:{name or ''}"
            for prefix in ("f", "pf") if file.production else ("f",):
                contributions[f"{prefix}:{target}"] = 1
                contributions[f"{prefix}:{target}:{file.module}"] = 1
        for module, name in set(references.inherited):
            contributions[f"i:{module}:{name}"] = 1
        for name, uses in file.attributes.items():
            contributions[f"a:{name}"] = len(uses)
            if file.production:
                contributions[f"p:{name}"] = len(uses)
        queries: set[str] = set()
        for candidate in file.candidates:
            if candidate.owner is None:
                for target in _function_targets(file.module, candidate.node.name):
                    for prefix in ("f", "pf"):
                        queries.update(
                            (f"{prefix}:{target}", f"{prefix}:{target}:{file.module}")
                        )
            else:
                queries.update(
                    (
                        f"a:{candidate.node.name}",
                        f"p:{candidate.node.name}",
                        f"i:{file.module}:{candidate.owner.name}",
                    )
                )
        return MemberSummary(
            self._fact_key(path),
            references.dependencies,
            contributions,
            sorted(queries),
            bool(file.candidates),
        )

    @cached_property
    def _files(self) -> list[_FileFacts]:
        syntax = self._syntax
        paths = sorted(
            {s.path for s in syntax.files.sources.values()} | set(syntax.files.tests)
        )
        missing = (
            None
            if syntax.cache is None
            else syntax.cache.missing([self._fact_key(path) for path in paths])
        )
        syntax.files.store.prefetch(
            syntax.files.root / path
            for path in paths
            if missing is None or self._fact_key(path) in missing
        )
        return [self._facts(path) for path in paths]

    @cached_property
    def _evidence(self) -> _UsageEvidence:
        evidence = _UsageEvidence({}, {}, {}, set())
        for file in self._files:
            references = self._references(file)
            for target in references.targets:
                evidence.module_uses.setdefault(target, set()).add(file.module)
                if file.production:
                    evidence.production_uses.setdefault(target, set()).add(file.module)
            for name, uses in file.attributes.items():
                evidence.attributes.setdefault(name, []).extend(
                    (file.module, line, owner, internal, file.production)
                    for line, owner, internal in uses
                )
            evidence.inherited.update(references.inherited)
        return evidence

    def _namespace_identity(self, module: str) -> tuple[str, str, bool] | None:
        files = self._syntax.files
        source = files.sources.get(module)
        if source is not None:
            return source.path, files.content_id(source.path), source.is_package
        package = files.packages.get(module)
        return (package.path, "namespace", False) if package is not None else None

    def _references(self, file: _FileFacts) -> _ResolvedReferences:
        syntax = self._syntax
        cache = syntax.cache
        key = (
            cache.key(
                "member-references",
                [
                    file.module,
                    file.path,
                    file.production,
                    syntax.files.content_id(file.path),
                ],
            )
            if cache
            else None
        )
        cached = cache.read(key, _REFERENCES) if cache and key else None
        if cached is not None and all(
            self._namespace_identity(module) == identity
            for module, identity in cached.dependencies.items()
        ):
            return cached
        dependencies: dict[str, tuple[str, str, bool] | None] = {}

        def namespace(module: str) -> Namespace | None:
            dependencies[module] = self._namespace_identity(module)
            return syntax.namespace(module)

        resolved: dict[str, tuple[str, str | None] | None] = {}

        def resolve(name: str) -> tuple[str, str | None] | None:
            if name not in resolved:
                resolved[name] = resolve_dotted(name, namespace)
            return resolved[name]

        imports, loads, stars = file.references
        targets: list[tuple[str, str | None]] = [(star, None) for star in stars]
        targets.extend(
            target
            for name in imports
            if (target := resolve(name)) is not None and target[1] is not None
        )
        targets.extend(
            target for name in loads if (target := resolve(name)) is not None
        )
        inherited = [
            target
            for name in file.bases
            if (target := resolve(name)) is not None and target[1] is not None
        ]
        result = _ResolvedReferences(targets, inherited, dependencies)
        if cache and key:
            cache.write(key, result, _REFERENCES)
        return result

    @cached_property
    def _attribute_counts(self) -> dict[str, tuple[int, int]]:
        return {
            name: (len(uses), sum(production for _, _, _, _, production in uses))
            for name, uses in self._evidence.attributes.items()
        }

    @cached_property
    def members(self) -> list[Member]:
        evidence = self._evidence
        members: list[Member] = []
        for file in self._files:
            states: list[MemberState] = []
            for candidate in file.candidates:
                owner = candidate.owner
                if owner is None:
                    external = self._function_external(file.module, candidate.node.name)
                    production_external = self._function_external(
                        file.module, candidate.node.name, production=True
                    )
                    references = candidate.internal_references
                    inherited = False
                else:
                    inherited = (file.module, owner.name) in evidence.inherited
                    local = [
                        line
                        for line, line_owner, internal in file.attributes.get(
                            candidate.node.name, []
                        )
                        if line_owner == owner.lineno and internal
                    ]
                    references = tuple(sorted(set(local)))
                    total, production = self._attribute_counts.get(
                        candidate.node.name, (0, 0)
                    )
                    external = total > len(local)
                    production_external = production > len(local)
                states.append((external, production_external, references, inherited))
            cache = self._syntax.cache
            if cache is None:
                members.extend(self._resolve_file(file, states))
            else:
                inputs = [
                    file.module,
                    file.path,
                    file.production,
                    self._syntax.files.content_id(file.path),
                    states,
                ]
                self._decision_keys[file.path] = cache.key("resolved-members", inputs)
                cached_members = cache.get(
                    "resolved-members",
                    inputs,
                    _MEMBERS,
                    partial(self._resolve_file, file, states),
                )
                members.extend(cached_members)
        return members

    @cached_property
    def member_files(self) -> dict[str, list[Member]]:
        groups: dict[str, list[Member]] = {}
        for member in self.members:
            groups.setdefault(member.path, []).append(member)
        return groups

    def decision_key(self, path: str) -> str:
        if self._syntax.cache is not None:
            return self._local_state.files[path].decision
        return self._decision_keys[path]

    def _resolve_file(
        self, file: _FileFacts, states: list[MemberState]
    ) -> list[Member]:
        return [
            replace(
                candidate,
                external_use=external,
                production_external_use=production,
                internal_references=references,
            )
            for candidate, (external, production, references, inherited) in zip(
                file.candidates, states, strict=True
            )
            if not inherited
        ]

    def _facts(self, path: str) -> _FileFacts:
        syntax = self._syntax
        source = syntax.files.source_for_path(path)

        def build() -> _FileFacts:
            module = syntax.for_path(path)
            if module is None:
                raise ValueError(f"missing syntax for indexed file: {path}")
            return _file_facts(module, syntax, production=source is not None)

        if syntax.cache is None:
            return build()
        key = self._fact_key(path)
        cached = syntax.cache.read(key, _FACTS)
        if cached is not None:
            return cached
        result = build()
        syntax.cache.write(key, result, _FACTS)
        return result

    def _fact_key(self, path: str) -> str:
        syntax = self._syntax
        source = syntax.files.source_for_path(path)
        if syntax.cache is None:
            return ""
        return syntax.cache.key(
            "members",
            [
                path,
                source.module if source else None,
                source.is_package if source else False,
                syntax.files.content_id(path),
            ],
        )

    def _function_external(
        self, module: str, name: str, *, production: bool = False
    ) -> bool:
        parts = module.split(".")
        targets = [
            (module, name),
            *((".".join(parts[:end]), None) for end in range(1, len(parts) + 1)),
        ]
        uses = (
            self._evidence.production_uses if production else self._evidence.module_uses
        )
        return any(
            users and (len(users) > 1 or module not in users)
            for target in targets
            if (users := uses.get(target))
        )

    def test_only_functions(
        self, tests: TestSyntaxIndex
    ) -> Iterator[tuple[Member, tuple[tuple[str, int], ...]]]:
        references: dict[tuple[str, str | None], set[tuple[str, int]]] = {}
        for path, test in tests.references():
            for line, qualified in [
                *test.imports,
                *((line, name) for line, name, _ in test.attributes),
            ]:
                target = resolve_dotted(qualified, self._syntax.namespace)
                if target is not None:
                    references.setdefault(target, set()).add((path, line))
        for member in self.members:
            if (
                member.kind == "function"
                and not member.node.name.startswith("_")
                and not member.production_external_use
                and (uses := references.get((member.module, member.node.name)))
            ):
                yield member, tuple(sorted(uses))

    @cached_property
    def attributes(self) -> list[AttributeMember]:
        result: list[AttributeMember] = []
        for file in self._files:
            for candidate in file.attribute_candidates:
                if (file.module, candidate.owner.name) in self._evidence.inherited:
                    continue
                local = sum(
                    owner == candidate.owner.lineno and internal
                    for _, owner, internal in file.attributes.get(candidate.name, [])
                )
                _, production = self._attribute_counts.get(candidate.name, (0, 0))
                if production > local:
                    continue
                result.append(candidate)
        return result

    def test_only_attributes(
        self, tests: TestSyntaxIndex
    ) -> Iterator[tuple[AttributeMember, tuple[tuple[str, int], ...]]]:
        references: dict[tuple[str, str, str], set[tuple[str, int]]] = {}
        for path, test in tests.references():
            for line, qualified, load in [*test.attributes, *test.instance_attributes]:
                if not load:
                    continue
                owner_name, _, name = qualified.rpartition(".")
                module, _, owner = owner_name.rpartition(".")
                target = resolve_export(module, owner, self._syntax.namespace)
                if target and target[1]:
                    references.setdefault((target[0], target[1], name), set()).add(
                        (path, line)
                    )
        for member in self.attributes:
            if uses := references.get((member.module, member.owner.name, member.name)):
                yield member, tuple(sorted(uses))


def _function_targets(module: str, name: str) -> Iterator[str]:
    yield f"{module}:{name}"
    parts = module.split(".")
    for end in range(1, len(parts) + 1):
        yield f"{'.'.join(parts[:end])}:"


def _file_facts(
    module: ModuleSyntax, syntax: SyntaxIndex, *, production: bool
) -> _FileFacts:
    visitor = _UseVisitor(module)
    visitor.visit(module.tree)
    nodes = list(ast.walk(module.tree))
    result = _FileFacts(
        module.module,
        module.path,
        production,
        [],
        reference_names(module),
        {
            name: [
                (use.line, use.owner.lineno if use.owner else None, use.internal)
                for use in uses
            ]
            for name, uses in visitor.attributes.items()
        },
        [
            qualified
            for node in nodes
            if isinstance(node, ast.ClassDef)
            for base in node.bases
            if (qualified := _qualified(base, module)) is not None
        ],
    )
    if not production:
        return result
    definitions = definition_bindings(module.tree)
    namespace = syntax.namespace(module.module)
    occupied = (
        namespace.defined | frozenset(namespace.imported) if namespace else frozenset()
    )
    exports = explicit_exports(module.tree)
    loads: dict[str, set[int]] = {}
    for node in nodes:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.setdefault(node.id, set()).add(node.lineno)
    for node in module.tree.body:
        if definitions.get(getattr(node, "name", "")) != [node]:
            continue
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if (
                not node.decorator_list
                and exports is not None
                and node.name not in exports
            ):
                result.candidates.append(
                    Member(
                        module.module,
                        module.path,
                        None,
                        Definition.of(node),
                        "function",
                        tuple(sorted(loads.get(node.name, ()))),
                        False,
                        occupied_names=occupied,
                        module_names=occupied,
                    )
                )
        elif isinstance(node, ast.ClassDef) and (
            not node.decorator_list
            and not node.keywords
            and all(
                _qualified(base, module) in {"object", "builtins.object"}
                for base in node.bases
            )
        ):
            result.candidates.extend(_methods_in(node, module, visitor, occupied))
            result.attribute_candidates.extend(
                _attribute_candidates(node, module, visitor)
            )
    return result


def _methods_in(
    owner: ast.ClassDef,
    module: ModuleSyntax,
    visitor: _UseVisitor,
    module_names: frozenset[str],
) -> Iterator[Member]:
    definitions = definition_bindings(ast.Module(body=owner.body, type_ignores=[]))
    occupied = frozenset(definitions) | frozenset(
        name
        for name, uses in visitor.attributes.items()
        if any(use.owner is owner and use.internal for use in uses)
    )
    shadows = _shadows_staticmethod(owner, module)
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
            and not shadows
        ):
            kind = "staticmethod"
        else:
            continue
        yield Member(
            module.module,
            module.path,
            Definition.of(owner),
            Definition.of(node),
            kind,
            (),
            False,
            False,
            kind == "staticmethod" and _class_dependencies(node, owner),
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
        self.instance: str | None = None
        self.attributes: dict[str, list[_AttributeUse]] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        owner, roots, depth, instance = (
            self.owner,
            self.roots,
            self.depth,
            self.instance,
        )
        self.owner = node
        self.roots = set()
        self.depth = 0
        self.instance = None
        self.generic_visit(node)
        self.owner = owner
        self.roots = roots
        self.depth = depth
        self.instance = instance

    def visit_FunctionDef(self, node: Function) -> None:
        previous = self.roots
        previous_instance = self.instance
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
        self.instance = (
            args[0].arg
            if self.owner and not self.depth and args and not decorators
            else None
        )
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1
        self.roots = previous
        self.instance = previous_instance

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
        definition = (
            node
            if isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id == self.instance
            and node.value.id in self.roots
            else None
        )
        self._record(node.attr, node.value, node.lineno, definition)
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

    def _record(
        self,
        name: str,
        expression: ast.expr,
        line: int,
        definition: ast.Attribute | None = None,
    ) -> None:
        self.attributes.setdefault(name, []).append(
            _AttributeUse(
                self.module,
                self.syntax.path,
                line,
                self.owner,
                isinstance(expression, ast.Name) and expression.id in self.roots,
                definition,
            )
        )


def _attribute_candidates(
    owner: ast.ClassDef, module: ModuleSyntax, visitor: _UseVisitor
) -> Iterator[AttributeMember]:
    definitions = definition_bindings(ast.Module(body=owner.body, type_ignores=[]))
    # Slots and custom attribute hooks make renaming a field a runtime contract.
    if {
        "__slots__",
        "__getattribute__",
        "__getattr__",
        "__setattr__",
        "__delattr__",
    }.intersection(definitions):
        return
    candidates: dict[
        str,
        tuple[
            ast.Attribute | ast.Name,
            Literal["instance-attribute", "class-attribute"],
        ],
    ] = {}
    for name, uses in visitor.attributes.items():
        for use in uses:
            if (
                use.owner is owner
                and use.definition is not None
                and name not in definitions
            ):
                candidates.setdefault(name, (use.definition, "instance-attribute"))
    for statement in owner.body:
        if not isinstance(statement, ast.Assign | ast.AnnAssign):
            continue
        # A call may construct a descriptor rather than ordinary class data.
        if statement.value is not None and not isinstance(
            statement.value,
            ast.Constant | ast.List | ast.Tuple | ast.Set | ast.Dict,
        ):
            continue
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
        )
        for target in targets:
            if isinstance(target, ast.Name) and definitions.get(target.id) == [target]:
                candidates.setdefault(target.id, (target, "class-attribute"))
    occupied = frozenset(definitions) | frozenset(
        name
        for name, uses in visitor.attributes.items()
        if any(use.owner is owner and use.internal for use in uses)
    )
    for name, (node, kind) in candidates.items():
        if name.startswith("_"):
            continue
        yield AttributeMember(
            module.module,
            module.path,
            Definition.of(owner),
            Definition(name, node.lineno, node.col_offset),
            name,
            kind,
            tuple(
                sorted(
                    {
                        use.line
                        for use in visitor.attributes.get(name, [])
                        if use.owner is owner and use.internal
                    }
                )
            ),
            occupied,
        )
