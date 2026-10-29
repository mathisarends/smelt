"""Known blocking/wait families and local syntactic deadline guards."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.test_deadlines import async_deadline, has_timeout

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


@dataclass(frozen=True)
class TestWait:
    node: ast.Call | ast.Await
    qualified: str
    provenance: str


def unbounded_waits(test: TestFunction, helpers: list[str]) -> list[TestWait]:
    calls = {call.node: call.qualified for call in test.calls}
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
            if isinstance(call.node, ast.Call) and not has_timeout(
                call.node, position=position
            ):
                unbounded.append(call)
            continue
        deadline = async_deadline(call.node, calls, parents, helpers)
        if deadline.awaited and not deadline.bounded:
            unbounded.append(call)
    return unbounded
