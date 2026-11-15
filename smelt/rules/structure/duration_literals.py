from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.bindings import qualified_name
from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

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


class PreferNamedDuration(Rule):
    code = "SMT358"
    name = "prefer-named-duration"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A timeout or delay is written as an inline number.",
        rationale=(
            "asyncio.timeout(2.5) at two call sites drifts apart as soon as "
            "one of them is tuned, and the bare number hides its unit and "
            "purpose. Ruff's PLR2004 only covers comparisons. This opt-in "
            "review reports int and float literals passed as the duration of "
            "asyncio.timeout, asyncio.sleep, asyncio.wait_for, asyncio.wait, "
            "time.sleep and the anyio and trio sleep and timeout scopes, "
            "resolved through imports and aliases, and literals added to a "
            "clock reading such as loop.time() + 10 or time.monotonic() + 10. "
            "sleep(0), which yields control instead of waiting, and tests are "
            "not checked."
        ),
        bad=("async with asyncio.timeout(2.5):\n    await page.settle()"),
        good=(
            "SETTLE_TIMEOUT_SECONDS = 2.5\n\n"
            "async with asyncio.timeout(SETTLE_TIMEOUT_SECONDS):\n"
            "    await page.settle()"
        ),
        fix=(
            "Move the value into a module constant or configuration field "
            "whose name states its purpose and unit, and reuse it at every "
            "call site that means the same wait. No automatic rewrite is "
            "performed. Enable rules: {prefer-named-duration: warning}, or "
            "select SMT358."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            syntax = ctx.syntax.for_path(source.path)
            if syntax is None:
                continue
            found: list[tuple[ast.expr, str]] = []
            for node in ast.walk(syntax.tree):
                if isinstance(node, ast.Call):
                    found.extend(_call_literals(node, syntax.bindings))
                elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                    found.extend(_deadline_literals(node, syntax.bindings))
            for literal, target in sorted(
                found, key=lambda item: (item[0].lineno, item[0].col_offset)
            ):
                value = ast.unparse(literal)
                yield self.violation(
                    f"Inline duration {value} in {target}",
                    path=source.path,
                    line=literal.lineno,
                    column=literal.col_offset + 1,
                    source_module=source.module,
                    expected={
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
    if value is None or not _number(value):
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
        if _number(value) and _clock(clock, bindings):
            yield value, f"{ast.unparse(clock)} + ..."


def _clock(node: ast.expr, bindings: Mapping[str, str]) -> bool:
    if not isinstance(node, ast.Call) or node.args or node.keywords:
        return False
    if qualified_name(node.func, bindings) in _CLOCKS:
        return True
    # Event loop clocks: loop.time(), asyncio.get_running_loop().time().
    return isinstance(node.func, ast.Attribute) and node.func.attr == "time"


def _number(node: ast.expr) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        node = node.operand
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    )


def _zero(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == 0
