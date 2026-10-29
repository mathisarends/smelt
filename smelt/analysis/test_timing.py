"""Known blocking/wait families and local syntactic deadline guards."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smelt.analysis.test_syntax import TestFunction

_PROCESS_WAITS = frozenset(
    {
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen.wait",
        "subprocess.Popen.communicate",
    }
)
_ASYNC_WAITS = frozenset({"asyncio.Event.wait", "asyncio.locks.Event.wait"})
_CONTEXT_TIMEOUTS = frozenset(
    {
        "asyncio.timeout",
        "asyncio.timeout_at",
        "anyio.fail_after",
        "anyio.move_on_after",
        "trio.fail_after",
        "trio.move_on_after",
    }
)


def _timeout(
    call: ast.Call, name: str = "timeout", position: int | None = None
) -> bool:
    value = next((kw.value for kw in call.keywords if kw.arg == name), None)
    if value is None and position is not None and len(call.args) > position:
        value = call.args[position]
    return value is not None and not (
        isinstance(value, ast.Constant) and value.value is None
    )


@dataclass(frozen=True)
class TestWait:
    node: ast.Call | ast.Await
    qualified: str
    provenance: str


def unbounded_waits(test: TestFunction, helpers: list[str]) -> list[TestWait]:
    calls = {call.node: call for call in test.calls}
    parents = {
        child: node
        for node in ast.walk(test.node)
        for child in ast.iter_child_nodes(node)
    }
    unbounded: list[TestWait] = []
    waits = [
        TestWait(call.node, call.qualified, call.provenance)
        for call in test.calls
        if call.qualified in _PROCESS_WAITS | _ASYNC_WAITS
    ]
    waits.extend(
        TestWait(node, qualified, provenance)
        for node, qualified, provenance in test.awaited_tasks
    )
    for call in waits:
        if call.qualified in _PROCESS_WAITS:
            # Async cancellation cannot interrupt a blocking synchronous process call.
            position = (
                0
                if call.qualified == "subprocess.Popen.wait"
                else 1
                if call.qualified == "subprocess.Popen.communicate"
                else None
            )
            if isinstance(call.node, ast.Call) and not _timeout(
                call.node, position=position
            ):
                unbounded.append(call)
            continue
        parent = parents.get(call.node)
        awaited = isinstance(call.node, ast.Await)
        bounded = False
        while parent is not None and parent is not test.node:
            awaited |= isinstance(parent, ast.Await)
            if isinstance(parent, ast.Call) and parent in calls:
                wrapper = calls[parent].qualified
                bounded |= wrapper == "asyncio.wait_for" and _timeout(
                    parent, position=1
                )
                bounded |= wrapper in helpers
            if isinstance(parent, ast.With | ast.AsyncWith):
                for item in parent.items:
                    context = item.context_expr
                    if isinstance(context, ast.Call) and context in calls:
                        wrapper = calls[context].qualified
                        keyword = (
                            "when"
                            if wrapper == "asyncio.timeout_at"
                            else "seconds"
                            if wrapper.startswith("trio.")
                            else "delay"
                        )
                        bounded |= wrapper in _CONTEXT_TIMEOUTS and _timeout(
                            context, keyword, 0
                        )
                        bounded |= wrapper in helpers
            parent = parents.get(parent)
        if awaited and not bounded:
            unbounded.append(call)
    return unbounded
