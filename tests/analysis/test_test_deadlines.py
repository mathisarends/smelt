import ast

import pytest

from smelt.analysis.test_deadlines import async_deadline
from smelt.analysis.test_syntax import (
    _analyze_test_syntax,  # smelt: ignore[SMT420] -- Unit-test this parser boundary.
)


@pytest.mark.parametrize(
    ("body", "awaited", "bounded", "raises"),
    [
        ("await event.wait()", True, False, False),
        ("await asyncio.wait_for(event.wait(), 1)", True, True, True),
        ("await asyncio.wait_for(fut=event.wait(), timeout=1)", True, True, True),
        ("await asyncio.wait_for(event.wait(), None)", True, False, False),
        ("asyncio.wait_for(event.wait(), 1)", False, True, True),
        ("await asyncio.wait_for(await event.wait(), 1)", True, False, False),
        (
            "await asyncio.wait_for(work(), timeout=await event.wait())",
            True,
            False,
            False,
        ),
        ("await work(event.wait())", False, False, False),
        (
            "async with asyncio.timeout(1):\n        await event.wait()",
            True,
            True,
            True,
        ),
        (
            "async with asyncio.timeout(None):\n        await event.wait()",
            True,
            False,
            False,
        ),
        (
            "async with asyncio.timeout(await event.wait()):\n        pass",
            True,
            False,
            False,
        ),
        ("with anyio.fail_after(1):\n        await event.wait()", True, True, True),
        ("with anyio.move_on_after(1):\n        await event.wait()", True, True, False),
        (
            "async with asyncio.timeout(2):\n        with anyio.move_on_after(1):\n            await event.wait()",
            True,
            True,
            False,
        ),
        ("async with deadline():\n        await event.wait()", True, True, False),
    ],
)
def test_deadlines_distinguish_awaiting_bounding_and_outcome_evidence(
    body: str, awaited: bool, bounded: bool, raises: bool
) -> None:
    [test] = _analyze_test_syntax(
        ast.parse(
            "import asyncio\nimport anyio\nfrom tests.timing import deadline\n"
            "async def test_event():\n    event = asyncio.Event()\n    " + body
        ),
        "tests/test_event.py",
    ).tests
    calls = {call.node: call.qualified for call in test.calls}
    parents = {
        child: parent
        for parent in ast.walk(test.node)
        for child in ast.iter_child_nodes(parent)
    }
    [wait] = [
        call.node for call in test.calls if call.qualified == "asyncio.Event.wait"
    ]
    guard = async_deadline(wait, calls, parents, ["tests.timing.deadline"])
    assert (guard.awaited, guard.bounded, guard.raises_on_timeout) == (
        awaited,
        bounded,
        raises,
    )
