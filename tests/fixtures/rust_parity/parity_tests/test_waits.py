import asyncio
import subprocess
import threading

import anyio
import pytest
import trio

from parity.test_support.checks import guard


def test_blocking_calls():
    subprocess.run(["true"])
    subprocess.call(["true"])
    subprocess.check_call(["true"])
    subprocess.check_output(["true"])
    assert subprocess.run(["true"], timeout=1)
    assert subprocess.run(["true"], timeout=None)


def test_popen():
    process = subprocess.Popen(["true"])
    process.wait()
    process.wait(1)
    process.wait(timeout=1)
    process.communicate()
    process.communicate(b"x")
    process.communicate(b"x", 1)
    assert process.communicate(timeout=1)


def test_threading_is_not_a_family():
    event = threading.Event()
    assert event.wait()


async def test_unbounded_event():
    event = asyncio.Event()
    await event.wait()
    assert event


async def test_not_awaited():
    event = asyncio.Event()
    waiter = event.wait()
    assert waiter


async def test_wait_for():
    event = asyncio.Event()
    await asyncio.wait_for(event.wait(), 1)
    await asyncio.wait_for(event.wait(), timeout=1)
    await asyncio.wait_for(fut=event.wait(), timeout=1)
    await asyncio.wait_for(event.wait(), None)
    await asyncio.wait_for(event.wait(), timeout=None)
    assert event


async def test_contexts():
    event = asyncio.Event()
    async with asyncio.timeout(1):
        await event.wait()
    async with asyncio.timeout(None):
        await event.wait()
    async with asyncio.timeout_at(1):
        await event.wait()
    async with asyncio.timeout_at(when=1):
        await event.wait()
    with anyio.fail_after(1):
        await event.wait()
    with anyio.move_on_after(1):
        await event.wait()
    with trio.fail_after(1):
        await event.wait()
    with trio.move_on_after(seconds=1):
        await event.wait()
    assert event


async def test_move_on_only():
    event = asyncio.Event()
    with anyio.move_on_after(1):
        await event.wait()


async def test_fail_after_counts():
    event = asyncio.Event()
    with anyio.fail_after(1):
        await event.wait()


async def test_configured_guard():
    event = asyncio.Event()
    await guard(event.wait())
    assert event


async def test_tasks(work):
    task = asyncio.create_task(work())
    await task
    future = asyncio.ensure_future(work())
    await future
    await asyncio.wait_for(task, 1)
    async with asyncio.timeout(1):
        await future
    assert task


async def test_async_subprocess():
    process = await asyncio.create_subprocess_exec("true")
    await process.wait()
    await process.communicate()
    shell = await asyncio.create_subprocess_shell("true")
    await asyncio.wait_for(shell.wait(), 1)
    assert process


def waits_forever():
    subprocess.run(["true"])


async def awaits_forever():
    await asyncio.Event().wait()


def bounded_helper():
    subprocess.run(["true"], timeout=1)


async def blocks_in_coroutine():
    subprocess.run(["true"])


def test_helper_wait():
    waits_forever()
    waits_forever()
    assert bounded_helper() is None


async def test_async_helper_wait():
    await awaits_forever()
    assert True is not None


async def test_guarded_helper():
    async with asyncio.timeout(1):
        await awaits_forever()
    assert True is not None


async def test_guard_cannot_stop_blocking():
    async with asyncio.timeout(1):
        await blocks_in_coroutine()
    assert True is not None


def test_clock_setattr(monkeypatch):
    loop = asyncio.get_event_loop()
    monkeypatch.setattr(loop, "time", lambda: 1)
    assert loop


def test_clock_assignment():
    loop = asyncio.get_running_loop()
    loop.time = lambda: 1
    assert loop


def test_other_setattr(monkeypatch):
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "other", lambda: 1)
    assert loop


@pytest.fixture
def waits_in_fixture():
    subprocess.run(["true"])
