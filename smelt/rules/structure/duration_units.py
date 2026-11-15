from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import SourceFile

type _Function = ast.FunctionDef | ast.AsyncFunctionDef

_DURATION_WORDS = frozenset({"timeout", "delay", "interval", "duration", "wait"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_EXEMPT_DECORATORS = frozenset({"overload", "override"})
_WRAPPERS = frozenset({"Final", "ClassVar", "Optional", "Annotated"})
_NUMBERS = frozenset({"int", "float"})


@dataclass(frozen=True, slots=True)
class _Duration:
    kind: str
    name: str
    node: ast.stmt | ast.arg


class MissingDurationUnit(Rule):
    code = "SMT357"
    name = "missing-duration-unit"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A numeric duration is named without its unit.",
        rationale=(
            "A constant called _FRAME_TIMEOUT = 2.0 does not say whether it "
            "means seconds or milliseconds, and code that mixes both converts "
            "at every call site until one conversion is forgotten. This opt-in "
            "review reports module constants, class fields and function "
            "parameters whose name ends in timeout, delay, interval, duration "
            "or wait and that hold a number: an int or float annotation, "
            "possibly wrapped in Final, ClassVar, Optional, Annotated or "
            "| None, or an unannotated int or float literal. Names that end in "
            "a unit, such as _TIMEOUT_SECONDS or quiet_ms, and values typed as "
            "timedelta are valid. @overload and @override signatures and tests "
            "are not checked."
        ),
        bad=("_FRAME_TIMEOUT = 2.0\n\n\ndef wait(delay: float) -> None:\n    ..."),
        good=(
            "_FRAME_TIMEOUT_SECONDS = 2.0\n\n\n"
            "def wait(delay_seconds: float) -> None:\n"
            "    ..."
        ),
        fix=(
            "Append the unit to the name, e.g. _SECONDS or _ms, and convert "
            "once where the value enters the code, or type the value as "
            "datetime.timedelta. No automatic rewrite is performed. Enable "
            "rules: {missing-duration-unit: warning}, or select SMT357."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            syntax = ctx.syntax.for_path(source.path)
            if syntax is None:
                continue
            durations = sorted(
                _durations(syntax.tree),
                key=lambda item: (item.node.lineno, item.node.col_offset),
            )
            for duration in durations:
                yield self._finding(source, duration)

    def _finding(self, source: SourceFile, duration: _Duration) -> Violation:
        suffix = "_SECONDS" if duration.name.isupper() else "_seconds"
        return self.violation(
            f"{duration.kind.capitalize()} {duration.name} holds a duration "
            "without a unit",
            path=source.path,
            line=duration.node.lineno,
            column=duration.node.col_offset + 1,
            source_module=source.module,
            expected={
                "kind": duration.kind,
                "name": duration.name,
                "suggested_name": duration.name + suffix,
                "automatic_rewrite": False,
            },
            hint=(
                f"Name it {duration.name + suffix} or after its actual unit, "
                "or type it as datetime.timedelta."
            ),
        )


def _durations(tree: ast.Module) -> Iterator[_Duration]:
    yield from _assignments(tree.body, "constant")
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            yield from _assignments(node.body, "field")
        elif isinstance(node, _FUNCTIONS) and not _exempt(node):
            yield from _parameters(node)


def _assignments(body: list[ast.stmt], kind: str) -> Iterator[_Duration]:
    for statement in body:
        if isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            numeric = _numeric_annotation(statement.annotation) or (
                _numeric_literal(statement.value)
            )
            if numeric and _unitless(statement.target.id):
                yield _Duration(kind, statement.target.id, statement)
        elif isinstance(statement, ast.Assign) and _numeric_literal(statement.value):
            for target in statement.targets:
                if isinstance(target, ast.Name) and _unitless(target.id):
                    yield _Duration(kind, target.id, statement)


def _parameters(function: _Function) -> Iterator[_Duration]:
    arguments = function.args
    positional = [*arguments.posonlyargs, *arguments.args]
    defaults: list[ast.expr | None] = [
        *([None] * (len(positional) - len(arguments.defaults))),
        *arguments.defaults,
    ]
    pairs = [
        *zip(positional, defaults, strict=True),
        *zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True),
    ]
    for argument, default in pairs:
        numeric = (
            _numeric_annotation(argument.annotation)
            if argument.annotation is not None
            else _numeric_literal(default)
        )
        if numeric and _unitless(argument.arg):
            yield _Duration("parameter", argument.arg, argument)


def _exempt(function: _Function) -> bool:
    for decorator in function.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.attr if isinstance(target, ast.Attribute) else None
        if isinstance(target, ast.Name):
            name = target.id
        if name in _EXEMPT_DECORATORS:
            return True
    return False


def _unitless(name: str) -> bool:
    words = [word for word in name.lower().split("_") if word]
    return bool(words) and words[-1] in _DURATION_WORDS


def _numeric_annotation(annotation: ast.expr) -> bool:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return False
    if isinstance(annotation, ast.Subscript) and _name(annotation.value) in _WRAPPERS:
        inner = annotation.slice
        if isinstance(inner, ast.Tuple) and inner.elts:
            inner = inner.elts[0]
        return _numeric_annotation(inner)
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        sides = [annotation.left, annotation.right]
        kept = [side for side in sides if not _is_none(side)]
        return len(kept) == 1 and _numeric_annotation(kept[0])
    return _name(annotation) in _NUMBERS


def _numeric_literal(value: ast.expr | None) -> bool:
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
        value = value.operand
    return (
        isinstance(value, ast.Constant)
        and isinstance(value.value, (int, float))
        and not isinstance(value.value, bool)
    )


def _name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None
