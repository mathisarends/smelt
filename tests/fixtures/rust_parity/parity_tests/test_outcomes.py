import asyncio
import subprocess
import sys
import unittest
import warnings
from unittest.mock import AsyncMock, Mock

import pytest

from parity.test_support.checks import check_result
from parity.test_support.service import Service, normalize


def test_no_assertion():
    normalize("A")


def test_assert_true_only():
    assert True


def test_assert_expression():
    assert normalize("A")


def test_raises_context():
    with pytest.raises(ValueError):
        normalize("A")


def test_raises_call():
    pytest.raises(ValueError, normalize, "A")


def test_raises_without_body():
    pytest.raises(ValueError)


def test_warns_forms():
    with pytest.warns(UserWarning):
        warnings.warn("x", stacklevel=1)


def test_warns_bare():
    pytest.warns(UserWarning)


def test_deprecated_forms():
    pytest.deprecated_call(normalize, "A")


def test_deprecated_bare():
    pytest.deprecated_call()


def test_configured_helper():
    check_result(normalize("A"))


def test_mock_assertions():
    called = Mock()
    called()
    called.assert_called_once()


def test_mock_not_a_mock(thing):
    thing.assert_called_once()


class TestCaseStyle(unittest.TestCase):
    def test_assert_method(self):
        self.assertEqual(normalize("A"), "a")

    def test_no_check(self):
        normalize("A")


class IsolatedStyle(unittest.IsolatedAsyncioTestCase):
    async def test_async_method(self):
        self.assertTrue(await Service().fetch())


async def test_event_bounded():
    event = asyncio.Event()
    async with asyncio.timeout(1):
        await event.wait()


async def test_event_wait_for():
    event = asyncio.Event()
    await asyncio.wait_for(event.wait(), 1)


async def test_event_unbounded():
    event = asyncio.Event()
    await event.wait()


def test_child_assertion():
    subprocess.run([sys.executable, "-c", "assert 1 == 1"], check=True)


def test_child_flags():
    subprocess.check_call([sys.executable, "-P", "-I", "-c", "assert 1 == 1"])


def test_child_named_script():
    script = "assert 1 == 1"
    subprocess.check_output((sys.executable, "-c", script))


def test_child_unchecked():
    subprocess.run([sys.executable, "-c", "assert 1 == 1"])


def test_child_true_only():
    subprocess.run([sys.executable, "-c", "assert True"], check=True)


def test_child_invalid_script():
    subprocess.run([sys.executable, "-c", "assert ("], check=True)


def test_child_swallowed():
    try:
        subprocess.run([sys.executable, "-c", "assert 1"], check=True)
    except subprocess.CalledProcessError:
        pass


def test_child_optimized():
    subprocess.run(
        [sys.executable, "-c", "assert 1"],
        check=True,
        env={"PYTHONOPTIMIZE": "1"},
    )


def test_child_clean_environment():
    subprocess.run(
        [sys.executable, "-c", "assert 1"],
        check=True,
        env={"PYTHONOPTIMIZE": "0"},
    )


def test_child_executable():
    subprocess.run(
        [sys.executable, "-c", "assert 1"], check=True, executable="/bin/python"
    )


def test_child_shell():
    subprocess.run([sys.executable, "-c", "assert 1"], check=True, shell=True)


def test_child_dynamic(script):
    subprocess.run([sys.executable, "-c", script], check=True)


def test_child_replaced_interpreter():
    sys.executable = "python"
    subprocess.run([sys.executable, "-c", "assert 1"], check=True)


def assert_shape(value):
    assert value


def swallowed_shape(value):
    try:
        assert value
    except AssertionError:
        return None


def no_evidence(value):
    return value


async def assert_async(value):
    assert value


def test_one_hop():
    assert_shape(1)


def test_swallowed_hop():
    swallowed_shape(1)


def test_caller_swallows():
    try:
        assert_shape(1)
    except AssertionError:
        pass


def test_empty_hop():
    no_evidence(1)


def test_imported_body():
    normalize("A")


async def test_awaited_coroutine():
    await assert_async(1)


def test_run_coroutine():
    asyncio.run(assert_async(1))


def test_unawaited_coroutine():
    assert_async(1)


def test_coroutine_passed_on(runner):
    runner(assert_async(1))


async def test_mock_only():
    fake = AsyncMock()
    await fake()
