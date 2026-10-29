import ast

import pytest

from smelt.analysis.test_syntax import analyze_test_syntax
from smelt.analysis.test_timing import unbounded_waits


@pytest.mark.parametrize(
    ("body", "flagged"),
    [
        ("subprocess.run(command, check=True)", True),
        ("subprocess.run(command, timeout=5)", False),
        ("subprocess.run(command, timeout=None)", True),
        ("async with asyncio.timeout(1):\n        subprocess.run(command)", True),
        ("process = subprocess.Popen(command)\n    process.communicate()", True),
        ("process = subprocess.Popen(command)\n    process.wait(timeout=1)", False),
        ("event = asyncio.Event()\n    await event.wait()", True),
        ("await asyncio.Event().wait()", True),
        (
            "event = asyncio.Event()\n    async with asyncio.timeout_at(when=deadline):\n        await event.wait()",
            False,
        ),
        ("process = subprocess.Popen(command)\n    process.wait(1)", False),
        ("event = asyncio.Event()\n    await asyncio.wait_for(event.wait(), 1)", False),
        (
            "event = asyncio.Event()\n    await asyncio.wait_for(event.wait(), timeout=None)",
            True,
        ),
        (
            "event = asyncio.Event()\n    async with asyncio.timeout(1):\n        await event.wait()",
            False,
        ),
        (
            "event = asyncio.Event()\n    async with asyncio.timeout(None):\n        await event.wait()",
            True,
        ),
        (
            "event = asyncio.Event()\n    with anyio.fail_after(1):\n        await event.wait()",
            False,
        ),
        (
            "event = asyncio.Event()\n    async with deadline():\n        await event.wait()",
            False,
        ),
        ("task = asyncio.create_task(work())\n    await task", True),
        (
            "task = asyncio.create_task(work())\n    async with asyncio.timeout(1):\n        await task",
            False,
        ),
        ("event = unknown()\n    await event.wait()", False),
        ("event = asyncio.Event()\n    pending = event.wait()", False),
        (
            "process = await asyncio.create_subprocess_exec('tool')\n    await process.wait()",
            True,
        ),
        (
            "process = await asyncio.create_subprocess_shell('tool')\n    await process.communicate()",
            True,
        ),
        ("process = await spawn('tool')\n    await process.wait()", True),
        (
            "process = asyncio.create_subprocess_exec('tool')\n    await process.wait()",
            False,
        ),
        ("process: asyncio.subprocess.Process\n    await process.wait()", True),
        ("process: Child\n    await process.communicate()", True),
        (
            "process = await asyncio.create_subprocess_exec('tool')\n    await asyncio.wait_for(process.wait(), 5)",
            False,
        ),
        (
            "process = await asyncio.create_subprocess_exec('tool')\n    async with asyncio.timeout(5):\n        await process.communicate()",
            False,
        ),
        (
            "process = await asyncio.create_subprocess_exec('tool')\n    await asyncio.wait_for(process.communicate(), 5)\n    await process.wait()",
            True,
        ),
        (
            "process = await asyncio.create_subprocess_exec('tool')\n    process.kill()\n    await process.wait()",
            True,
        ),
        (
            "process = await asyncio.create_subprocess_exec('tool')\n    pending = process.wait()",
            False,
        ),
        ("process = unknown()\n    await process.wait()", False),
        (
            "async with asyncio.timeout(1):\n        await asyncio.sleep(0.01)\n    await asyncio.sleep(0)",
            False,
        ),
    ],
)
def test_known_waits_and_local_deadlines(body: str, flagged: bool) -> None:
    source = (
        "import subprocess\nimport asyncio\nimport anyio\nfrom asyncio import create_subprocess_exec as spawn\nfrom asyncio.subprocess import Process as Child\nfrom tests.timing import deadline\nasync def test_run():\n    "
        + body
    )
    [test] = analyze_test_syntax(ast.parse(source), "tests/test_run.py").tests
    assert bool(unbounded_waits(test, ["tests.timing.deadline"])) is flagged


@pytest.mark.parametrize(
    ("body", "flagged"),
    [
        ("loop = asyncio.get_running_loop()\n    loop.time = lambda: 0.0", True),
        (
            "loop = asyncio.get_event_loop()\n    monkeypatch.setattr(loop, 'time', frozen)",
            True,
        ),
        ("clock = domain_clock()\n    clock.time = lambda: 0.0", False),
        ("loop = unknown()\n    loop.time = lambda: 0.0", False),
    ],
)
def test_scheduler_clock_provenance_is_distinct_from_domain_time(
    body: str, flagged: bool
) -> None:
    [test] = analyze_test_syntax(
        ast.parse("import asyncio\ndef test_run(monkeypatch):\n    " + body),
        "tests/test_run.py",
    ).tests
    assert bool(test.clock_replacements) is flagged
