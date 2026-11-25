from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.bindings import last_name, qualified_name
from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import tree_nodes
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from smelt.analysis.files import SourceFile

type _Function = ast.FunctionDef | ast.AsyncFunctionDef

_DURATION_WORDS = frozenset({"timeout", "delay", "interval", "duration", "wait"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_EXEMPT_DECORATORS = frozenset({"overload", "override"})
_WRAPPERS = frozenset({"Final", "ClassVar", "Optional", "Annotated"})
_NUMBERS = frozenset({"int", "float"})
# Qualified call -> (keyword, position) of its relative duration argument.
_DURATION_CALLS: dict[str, tuple[str, int | None]] = {
    "asyncio.timeout": ("delay", 0),
    "asyncio.sleep": ("delay", 0),
    "asyncio.wait_for": ("timeout", 1),
    "asyncio.wait": ("timeout", None),
    "time.sleep": ("secs", 0),
    "anyio.sleep": ("delay", 0),
    "anyio.fail_after": ("delay", 0),
    "anyio.move_on_after": ("delay", 0),
    "trio.sleep": ("seconds", 0),
    "trio.fail_after": ("seconds", 0),
    "trio.move_on_after": ("seconds", 0),
}
# sleep(0) yields control to the event loop rather than waiting.
_SLEEPS = frozenset({"asyncio.sleep", "anyio.sleep", "trio.sleep", "time.sleep"})
_CLOCKS = frozenset({"time.monotonic", "time.time", "time.perf_counter"})


@dataclass(frozen=True, slots=True)
class _Duration:
    kind: str
    name: str
    node: ast.stmt | ast.arg


class ImplicitDurationUnit(Rule):
    code = "MOD010"
    name = "modeling/implicit-duration-unit"
    category = Category.MODELING
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary=(
            "A numeric duration is named without its unit or written as an inline "
            "number."
        ),
        rationale=(
            "A constant called _FRAME_TIMEOUT = 2.0 does not say whether it means "
            "seconds or milliseconds, and asyncio.timeout(2.5) at two call sites "
            "drifts apart as soon as one of them is tuned. This opt-in review "
            "reports two forms. Names (kind constant, field or parameter): module "
            "constants, class fields and function parameters whose name ends in "
            "timeout, delay, interval, duration or wait and that hold a number: an "
            "int or float annotation, possibly wrapped in Final, ClassVar, "
            "Optional, Annotated or | None, or an unannotated int or float "
            "literal. Names that end in a unit, such as _TIMEOUT_SECONDS or "
            "quiet_ms, and values typed as timedelta are valid; @overload and "
            "@override signatures are not checked. Literals (kind literal): int "
            "and float literals passed as the duration of asyncio.timeout, "
            "asyncio.sleep, asyncio.wait_for, asyncio.wait, time.sleep and the "
            "anyio and trio sleep and timeout scopes, resolved through imports "
            "and aliases, and literals added to a clock reading such as "
            "loop.time() + 10 or time.monotonic() + 10. Ruff's PLR2004 only "
            "covers comparisons. sleep(0), which yields control instead of "
            "waiting, and tests are not checked."
        ),
        bad=(
            "_FRAME_TIMEOUT = 2.0\n\n\n"
            "async def settle(page):\n"
            "    async with asyncio.timeout(2.5):\n"
            "        await page.settle()"
        ),
        good=(
            "_FRAME_TIMEOUT_SECONDS = 2.0\n"
            "SETTLE_TIMEOUT_SECONDS = 2.5\n\n\n"
            "async def settle(page):\n"
            "    async with asyncio.timeout(SETTLE_TIMEOUT_SECONDS):\n"
            "        await page.settle()"
        ),
        fix=(
            "Append the unit to the name, e.g. _SECONDS or _ms, and convert once "
            "where the value enters the code, or type the value as "
            "datetime.timedelta. Move an inline duration into a module constant "
            "or configuration field whose name states its purpose and unit, and "
            "reuse it at every call site that means the same wait. No automatic "
            "rewrite is performed. Enable rules: {modeling/implicit-duration-unit: "
            "warning}, or select MOD010."
        ),
        canonical=(
            "Put the unit in duration names (timeout_seconds) or use "
            "timedelta; avoid inline numbers"
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            syntax = ctx.syntax.for_path(source.path)
            if syntax is None:
                continue
            found = [
                self._name(source, duration) for duration in _durations(syntax.tree)
            ]
            for node in syntax.nodes:
                if isinstance(node, ast.Call):
                    literals = _call_literals(node, syntax.bindings)
                elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                    literals = _deadline_literals(node, syntax.bindings)
                else:
                    continue
                found.extend(
                    self._literal(source, literal, target)
                    for literal, target in literals
                )
            yield from sorted(found, key=lambda item: (item.line, item.column))

    def _name(self, source: SourceFile, duration: _Duration) -> Violation:
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

    def _literal(self, source: SourceFile, literal: ast.expr, target: str) -> Violation:
        value = ast.unparse(literal)
        return self.violation(
            f"Inline duration {value} in {target}",
            path=source.path,
            line=literal.lineno,
            column=literal.col_offset + 1,
            source_module=source.module,
            expected={
                "kind": "literal",
                "value": value,
                "target": target,
                "automatic_rewrite": False,
            },
            hint=(
                f"Name {value} as a constant or setting that states its "
                "purpose and unit, and reuse it at matching call sites."
            ),
        )


def _call_literals(
    call: ast.Call, bindings: Mapping[str, str]
) -> Iterator[tuple[ast.expr, str]]:
    qualified = qualified_name(call.func, bindings)
    if qualified not in _DURATION_CALLS:
        return
    keyword, position = _DURATION_CALLS[qualified]
    value = next((item.value for item in call.keywords if item.arg == keyword), None)
    if value is None and position is not None and len(call.args) > position:
        value = call.args[position]
    if value is None or not _numeric_literal(value):
        return
    if qualified in _SLEEPS and _zero(value):
        return
    yield value, f"{qualified}()"


def _deadline_literals(
    addition: ast.BinOp, bindings: Mapping[str, str]
) -> Iterator[tuple[ast.expr, str]]:
    for clock, value in (
        (addition.left, addition.right),
        (addition.right, addition.left),
    ):
        if _numeric_literal(value) and _clock(clock, bindings):
            yield value, f"{ast.unparse(clock)} + ..."


def _clock(node: ast.expr, bindings: Mapping[str, str]) -> bool:
    if not isinstance(node, ast.Call) or node.args or node.keywords:
        return False
    if qualified_name(node.func, bindings) in _CLOCKS:
        return True
    # Event loop clocks: loop.time(), asyncio.get_running_loop().time().
    return isinstance(node.func, ast.Attribute) and node.func.attr == "time"


def _zero(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == 0


def _durations(tree: ast.Module) -> Iterator[_Duration]:
    yield from _assignments(tree.body, "constant")
    for node in tree_nodes(tree):
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
    if (
        isinstance(annotation, ast.Subscript)
        and last_name(annotation.value) in _WRAPPERS
    ):
        inner = annotation.slice
        if isinstance(inner, ast.Tuple) and inner.elts:
            inner = inner.elts[0]
        return _numeric_annotation(inner)
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        sides = [annotation.left, annotation.right]
        kept = [side for side in sides if not _is_none(side)]
        return len(kept) == 1 and _numeric_annotation(kept[0])
    return last_name(annotation) in _NUMBERS


def _numeric_literal(value: ast.expr | None) -> bool:
    if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
        value = value.operand
    return (
        isinstance(value, ast.Constant)
        and isinstance(value.value, (int, float))
        and not isinstance(value.value, bool)
    )


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None
