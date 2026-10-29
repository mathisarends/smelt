"""Local async deadline facts shared by outcome and timing review."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_CONTEXT_TIMEOUTS = {
    "asyncio.timeout": "delay",
    "asyncio.timeout_at": "when",
    "anyio.fail_after": "delay",
    "anyio.move_on_after": "delay",
    "trio.fail_after": "seconds",
    "trio.move_on_after": "seconds",
}


def has_timeout(
    call: ast.Call, name: str = "timeout", position: int | None = None
) -> bool:
    value = next((kw.value for kw in call.keywords if kw.arg == name), None)
    if value is None and position is not None and len(call.args) > position:
        value = call.args[position]
    return value is not None and not (
        isinstance(value, ast.Constant) and value.value is None
    )


@dataclass(frozen=True)
class AsyncDeadline:
    awaited: bool
    bounded: bool
    raises_on_timeout: bool


def async_deadline(
    node: ast.Call | ast.Await,
    calls: Mapping[ast.Call, str],
    parents: Mapping[ast.AST, ast.AST],
    helpers: Sequence[str] = (),
) -> AsyncDeadline:
    awaited = isinstance(node, ast.Await)
    bounded = False
    raises = False
    may_move_on = False
    awaitable: ast.AST | None = node if isinstance(node, ast.Call) else None
    child: ast.AST = node
    parent = parents.get(child)
    while parent is not None:
        if isinstance(parent, ast.Await) and child is awaitable:
            awaited = True
            awaitable = None
        if isinstance(parent, ast.Call):
            wrapper = calls.get(parent)
            wraps = awaitable is not None and (
                bool(parent.args and parent.args[0] is awaitable)
                or any(
                    kw.arg == "fut" and kw.value is awaitable for kw in parent.keywords
                )
            )
            if wraps and wrapper == "asyncio.wait_for":
                awaitable = parent
                if has_timeout(parent, position=1):
                    bounded = True
                    raises = True
            elif wraps and wrapper in helpers:
                awaitable = parent
                bounded = True
        if isinstance(parent, ast.With | ast.AsyncWith) and child in parent.body:
            for item in parent.items:
                context = item.context_expr
                if not isinstance(context, ast.Call):
                    continue
                wrapper = calls.get(context)
                keyword = _CONTEXT_TIMEOUTS.get(wrapper or "")
                if keyword and has_timeout(context, keyword, 0):
                    bounded = True
                    if wrapper and wrapper.endswith(".move_on_after"):
                        may_move_on = True
                    else:
                        raises = True
                bounded |= wrapper in helpers
        child, parent = parent, parents.get(parent)
    # A move_on_after scope may leave successfully before the event is observed.
    return AsyncDeadline(awaited, bounded, raises and not may_move_on)
