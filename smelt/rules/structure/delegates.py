from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

type _Function = ast.FunctionDef | ast.AsyncFunctionDef

_NEUTRAL_BASES = frozenset({"object", "ABC", "Protocol", "Generic"})


class RedundantPrivateDelegate(Rule):
    file_local = True
    code = "SMT343"
    name = "redundant-private-delegate"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A method only forwards its parameters to a private method used nowhere else.",
        rationale=(
            "def events(self, *, run_id): return self._events(run_id=run_id) adds "
            "a second name and a jump without behavior: calling an async "
            "generator already defers its body until iteration, so moving the "
            "body into events behaves the same. Validation before the call is the "
            "legitimate reason for the split, so only a body consisting of one "
            "return is reported. The private method must be an undecorated method "
            "of the same class with the same parameter names and kinds, receive "
            "every parameter unchanged, and be referenced nowhere else in the "
            "module. Dunder and decorated methods, private names defined twice, "
            "classes with bases from other modules (a base may call the private "
            "hook) and tests are not reported. Subclasses in other modules that "
            "override the private method are not analyzed."
        ),
        bad=(
            "class Frames:\n"
            "    def events(self, *, run_id: UUID) -> AsyncGenerator[Frame]:\n"
            "        return self._events(run_id=run_id)\n\n"
            "    async def _events(self, *, run_id: UUID) -> AsyncGenerator[Frame]:\n"
            "        async for frame in self._source(run_id):\n"
            "            yield frame"
        ),
        good=(
            "class Frames:\n"
            "    async def events(self, *, run_id: UUID) -> AsyncGenerator[Frame]:\n"
            "        async for frame in self._source(run_id):\n"
            "            yield frame"
        ),
        fix=(
            "Move the private method's body and async/def kind into the public "
            "method, keep the public name, signature and docstring, and delete the "
            "private method. Keep the split with a reasoned suppression when a "
            "subclass overrides the private method. No automatic rewrite is "
            "performed. Enable rules: {redundant-private-delegate: hint}, or "
            "select SMT343."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            module = _Module(tree)
            for delegate in module.delegates():
                caller, callee = delegate.caller, delegate.callee
                yield self.violation(
                    f"{caller.name} only forwards to {callee.name}",
                    path=source.path,
                    line=caller.lineno,
                    column=caller.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "class": delegate.owner.name,
                        "method": caller.name,
                        "delegate": callee.name,
                        "delegate_line": callee.lineno,
                        "automatic_rewrite": False,
                    },
                    hint=(
                        f"Move the body of {callee.name} into {caller.name} and "
                        f"delete {callee.name}."
                    ),
                )


@dataclass(frozen=True)
class _Delegate:
    owner: ast.ClassDef
    caller: _Function
    callee: _Function


class _Module:
    """Classes, defined functions and name references of one module."""

    def __init__(self, tree: ast.Module) -> None:
        self.classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        self.local_classes = {node.name: node for node in self.classes}
        self.definitions: Counter[str] = Counter()
        self.references: Counter[str] = Counter()
        for node in ast.walk(tree):
            match node:
                case ast.FunctionDef() | ast.AsyncFunctionDef():
                    self.definitions[node.name] += 1
                case ast.Attribute():
                    self.references[node.attr] += 1
                case ast.Name():
                    self.references[node.id] += 1
                case ast.Constant(value=str(text)):
                    self.references[text] += 1

    def delegates(self) -> Iterator[_Delegate]:
        for owner in self.classes:
            if not self._isolated(owner):
                continue
            methods = {
                node.name: node
                for node in owner.body
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            for caller in methods.values():
                callee_name = _forwarded(caller)
                callee = methods.get(callee_name) if callee_name else None
                if (
                    callee is not None
                    and callee is not caller
                    and not callee.decorator_list
                    and self.definitions[callee.name] == 1
                    and self.references[callee.name] == 1
                    and _same_parameters(caller, callee)
                ):
                    yield _Delegate(owner, caller, callee)

    def _isolated(self, owner: ast.ClassDef) -> bool:
        """No base class outside this module could call a private hook."""
        for base in owner.bases:
            match base:
                case ast.Subscript(value=ast.Name(id=name) | ast.Attribute(attr=name)):
                    local = None
                case ast.Name(id=name):
                    local = self.local_classes.get(name)
                case ast.Attribute(attr=name):
                    local = None
                case _:
                    return False
            if name in _NEUTRAL_BASES:
                continue
            if local is None or local is owner or not self._isolated(local):
                return False
        return True


def _forwarded(caller: _Function) -> str | None:
    """The private method a method body only returns a call of."""
    if _dunder(caller.name) or caller.decorator_list:
        return None
    body = caller.body
    if body and _docstring(body[0]):
        body = body[1:]
    receiver = _receiver(caller)
    match body:
        case [
            ast.Return(
                value=ast.Call(
                    func=ast.Attribute(value=ast.Name(id=owner), attr=name)
                ) as call
            )
        ] if owner == receiver and name.startswith("_") and not _dunder(name):
            return name if _forwards_all(caller, call) else None
    return None


def _receiver(function: _Function) -> str | None:
    arguments = [*function.args.posonlyargs, *function.args.args]
    return arguments[0].arg if arguments else None


def _forwards_all(caller: _Function, call: ast.Call) -> bool:
    args = caller.args
    positional = [arg.arg for arg in (*args.posonlyargs, *args.args)][1:]
    passed: list[str] = []
    for index, value in enumerate(call.args):
        match value:
            case ast.Name(id=name) if (
                index < len(positional) and name == positional[index]
            ):
                passed.append(name)
            case ast.Starred(value=ast.Name(id=name)) if (
                args.vararg is not None and name == args.vararg.arg
            ):
                passed.append(f"*{name}")
            case _:
                return False
    for keyword in call.keywords:
        match keyword:
            case ast.keyword(arg=None, value=ast.Name(id=name)) if (
                args.kwarg is not None and name == args.kwarg.arg
            ):
                passed.append(f"**{name}")
            case ast.keyword(arg=str(arg), value=ast.Name(id=name)) if arg == name:
                passed.append(name)
            case _:
                return False
    expected = [
        *positional,
        *(arg.arg for arg in args.kwonlyargs),
        *([f"*{args.vararg.arg}"] if args.vararg else []),
        *([f"**{args.kwarg.arg}"] if args.kwarg else []),
    ]
    return sorted(passed) == sorted(expected)


def _same_parameters(caller: _Function, callee: _Function) -> bool:
    def shape(function: _Function) -> tuple[object, ...]:
        args = function.args
        positional = [arg.arg for arg in (*args.posonlyargs, *args.args)]
        return (
            len(args.posonlyargs),
            positional[1:],
            args.vararg.arg if args.vararg else None,
            [arg.arg for arg in args.kwonlyargs],
            args.kwarg.arg if args.kwarg else None,
            bool(positional),
        )

    return shape(caller) == shape(callee)


def _docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")
