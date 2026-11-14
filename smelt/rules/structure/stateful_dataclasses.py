from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

_INITIALIZERS = frozenset({"__post_init__"})
_MUTABLE_CONTAINERS = frozenset(
    {
        "list",
        "dict",
        "set",
        "bytearray",
        "defaultdict",
        "deque",
        "Counter",
        "OrderedDict",
        "MutableMapping",
        "MutableSequence",
        "MutableSet",
    }
)
_MUTATING_METHODS = frozenset(
    {
        "add",
        "append",
        "appendleft",
        "clear",
        "difference_update",
        "discard",
        "extend",
        "extendleft",
        "insert",
        "intersection_update",
        "pop",
        "popitem",
        "popleft",
        "remove",
        "reverse",
        "setdefault",
        "sort",
        "symmetric_difference_update",
        "update",
    }
)


class StatefulDataclass(Rule):
    file_local = True
    code = "SMT349"
    name = "stateful-dataclass"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A dataclass mutates its own fields after construction.",
        rationale=(
            "A dataclass reads as a value: its generated __init__ is the "
            "constructor and its fields are what callers may rely on. When "
            "methods keep writing to self, the class is really a mutable "
            "accumulator or state machine with a value-like surface: callers "
            "can construct it in a half-built state, observe fields that are "
            "only scratch space for private helpers, and share instances whose "
            "contents change underneath them. Building the values first and "
            "passing them to a frozen dataclass, or keeping the accumulation "
            "in a plain class with private attributes, makes the lifecycle "
            "explicit. This opt-in review reports a @dataclass that is not "
            "frozen=True when a method other than __post_init__ assigns, "
            "deletes or augments an attribute of self, assigns or deletes an "
            "item of a field, calls a mutating container method such as "
            "append, add, update, pop or discard on a field declared as a "
            "mutable container, or calls setattr on self. Mutations inside "
            "nested functions of a method count. Calls that mutate through "
            "other objects, classes that are not dataclasses and tests are "
            "not reported. Intentional mutable records can keep a reasoned "
            "suppression."
        ),
        bad=(
            "@dataclass\n"
            "class InterruptedActivity:\n"
            "    turn: str | None = None\n"
            "    started: set[str] = field(default_factory=set)\n\n"
            "    @classmethod\n"
            "    def scan(cls, events: Iterable[Event]) -> Self:\n"
            "        activity = cls()\n"
            "        for event in events:\n"
            "            activity._observe(event)\n"
            "        return activity\n\n"
            "    def _observe(self, event: Event) -> None:\n"
            "        if isinstance(event, TurnStarted):\n"
            "            self.turn = event.turn_id\n"
            "        elif isinstance(event, ToolStarted):\n"
            "            self.started.add(event.call_id)"
        ),
        good=(
            "@dataclass(frozen=True)\n"
            "class InterruptedActivity:\n"
            "    turn: str | None\n"
            "    started: frozenset[str]\n\n"
            "    @classmethod\n"
            "    def scan(cls, events: Iterable[Event]) -> Self:\n"
            "        turn = None\n"
            "        started: set[str] = set()\n"
            "        for event in events:\n"
            "            if isinstance(event, TurnStarted):\n"
            "                turn = event.turn_id\n"
            "            elif isinstance(event, ToolStarted):\n"
            "                started.add(event.call_id)\n"
            "        return cls(turn=turn, started=frozenset(started))"
        ),
        fix=(
            "Compute the field values first and pass them to the constructor of "
            "a frozen dataclass, using immutable field types such as tuple, "
            "frozenset or Mapping; derive further values with properties or "
            "dataclasses.replace. When the object genuinely accumulates state, "
            "make it a plain class whose scratch attributes are private, or "
            "move the accumulation into a separate builder. No automatic "
            "rewrite is performed. Enable rules: {stateful-dataclass: hint}, "
            "or select SMT349. Package overrides and reasoned inline "
            "suppressions are supported."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            for found in _stateful_dataclasses(tree):
                name = found.owner.name
                methods = ", ".join(found.methods)
                yield self.violation(
                    f"Dataclass {name} mutates its own fields after "
                    f"construction in {methods}",
                    path=source.path,
                    line=found.owner.lineno,
                    column=found.owner.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "class": name,
                        "methods": list(found.methods),
                        "fields": list(found.fields),
                        "automatic_rewrite": False,
                    },
                    hint=(
                        f"Build the values before constructing {name} and make "
                        "it frozen, or keep the accumulation in a plain class "
                        "with private attributes."
                    ),
                )


@dataclass(frozen=True)
class _StatefulClass:
    owner: ast.ClassDef
    methods: tuple[str, ...]
    fields: tuple[str, ...]


def _stateful_dataclasses(tree: ast.Module) -> Iterator[_StatefulClass]:
    for owner in ast.walk(tree):
        if not isinstance(owner, ast.ClassDef) or not _is_mutable_dataclass(owner):
            continue
        containers = _container_fields(owner)
        methods: list[str] = []
        fields: set[str] = set()
        for method in owner.body:
            if (
                not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef)
                or method.name in _INITIALIZERS
                or not (receiver := _receiver(method))
            ):
                continue
            mutated = _mutated_attributes(method, receiver, containers)
            if mutated:
                methods.append(method.name)
                fields.update(mutated)
        if methods:
            yield _StatefulClass(owner, tuple(methods), tuple(sorted(fields)))


def _is_mutable_dataclass(owner: ast.ClassDef) -> bool:
    for decorator in owner.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        keywords = call.keywords if call else []
        target = call.func if call else decorator
        if _dotted(target).rpartition(".")[2] != "dataclass":
            continue
        frozen = next((item.value for item in keywords if item.arg == "frozen"), None)
        # An unknown frozen expression is not evidence of a mutable dataclass.
        return frozen is None or (
            isinstance(frozen, ast.Constant) and frozen.value is False
        )
    return False


def _container_fields(owner: ast.ClassDef) -> frozenset[str]:
    names: set[str] = set()
    for statement in owner.body:
        if not isinstance(statement, ast.AnnAssign) or not isinstance(
            statement.target, ast.Name
        ):
            continue
        if _is_container(statement.annotation) or _container_factory(statement.value):
            names.add(statement.target.id)
    return frozenset(names)


def _is_container(annotation: ast.expr) -> bool:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return False
    if isinstance(annotation, ast.Subscript):
        annotation = annotation.value
    return _dotted(annotation).rpartition(".")[2] in _MUTABLE_CONTAINERS


def _container_factory(value: ast.expr | None) -> bool:
    if not isinstance(value, ast.Call):
        return False
    return any(
        item.arg == "default_factory"
        and _dotted(item.value).rpartition(".")[2] in _MUTABLE_CONTAINERS
        for item in value.keywords
    )


def _receiver(method: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    decorators = {_dotted(item).rpartition(".")[2] for item in method.decorator_list}
    if decorators & {"staticmethod", "classmethod"}:
        return None
    arguments = [*method.args.posonlyargs, *method.args.args]
    return arguments[0].arg if arguments else None


def _mutated_attributes(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    receiver: str,
    containers: frozenset[str],
) -> set[str]:
    mutated: set[str] = set()
    for node in ast.walk(method):
        match node:
            case ast.Assign(targets=targets) | ast.Delete(targets=targets):
                for target in targets:
                    mutated.update(_written(target, receiver))
            case ast.AugAssign(target=target) | ast.AnnAssign(target=target):
                mutated.update(_written(target, receiver))
            case ast.Call(func=ast.Attribute(value=owner, attr=operation)):
                field_name = _self_attribute(owner, receiver)
                if field_name in containers and operation in _MUTATING_METHODS:
                    mutated.add(field_name)
            case ast.Call(func=ast.Name(id="setattr"), args=[ast.Name(id=name), *_]):
                if name == receiver:
                    mutated.add("<setattr>")
    return mutated


def _written(target: ast.expr, receiver: str) -> Iterator[str]:
    if isinstance(target, ast.Tuple | ast.List):
        for element in target.elts:
            yield from _written(element, receiver)
        return
    if isinstance(target, ast.Starred):
        yield from _written(target.value, receiver)
        return
    while isinstance(target, ast.Subscript):
        target = target.value
    if name := _self_attribute(target, receiver):
        yield name


def _self_attribute(node: ast.expr, receiver: str) -> str | None:
    match node:
        case ast.Attribute(value=ast.Name(id=name), attr=attribute) if name == receiver:
            return attribute
    return None


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (owner := _dotted(node.value)):
        return f"{owner}.{node.attr}"
    return ""
