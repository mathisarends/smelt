import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from parity.test_support import Service


async def test_call_only():
    fake = AsyncMock()
    await fake()
    fake.assert_called_once()


async def test_called_attribute():
    fake = AsyncMock()
    await fake()
    assert fake.called


async def test_call_count():
    fake = AsyncMock()
    await fake()
    assert fake.call_count == 1


async def test_call_args():
    fake = AsyncMock()
    await fake(1)
    args, kwargs = fake.call_args
    assert args == (1,) and not kwargs


async def test_call_args_list():
    fake = AsyncMock()
    await fake(1)
    calls = fake.call_args_list
    assert calls


async def test_not_asserted_read():
    fake = AsyncMock()
    await fake()
    count = fake.call_count
    print(count)
    assert fake is not None


async def test_await_evidence():
    fake = AsyncMock()
    await fake()
    fake.assert_called_once()
    fake.assert_awaited_once()


async def test_await_count():
    fake = AsyncMock()
    await fake()
    assert fake.called
    assert fake.await_count == 1


async def test_negative_checks():
    fake = AsyncMock()
    fake.assert_not_called()
    assert not fake.called
    assert fake.call_count == 0
    assert 0 == fake.call_count
    assert fake.call_args is None
    assert fake.called is False
    assert fake.call_args_list == []
    assert fake.call_count == 0.0
    assert fake.call_count == 1


async def test_spec_members(spec_mock, autospec, typed_mock):
    spec_mock.fetch()
    spec_mock.fetch.assert_called_once()
    spec_mock.plain()
    spec_mock.plain.assert_called_once()
    autospec.build()
    autospec.build.assert_called_once()
    typed_mock.declared()
    typed_mock.declared.assert_called_once()
    typed_mock.excluded.assert_called_once()


async def test_local_spec():
    fake = MagicMock(spec=Service)
    other = Mock(spec_set=Service)
    positional = Mock(Service)
    fake.fetch.assert_called()
    other.qualified.assert_called()
    positional.fetch.assert_called()


async def test_spec_replaced():
    fake = MagicMock(spec=Service)
    fake.fetch = 1
    fake.fetch.assert_called()


async def test_alias():
    fake = AsyncMock()
    alias = fake
    alias.assert_called_once()
    fake.assert_awaited_once()


async def test_child_mock():
    fake = AsyncMock()
    fake.child.assert_called_once()
    fake.other.assert_awaited()
    fake.other.assert_called()


async def test_patch_result():
    with patch("parity.test_support.service.normalize") as patched:
        patched.assert_called_once()
    with patch("parity.test_support.service.normalize", new=AsyncMock()) as replaced:
        replaced.assert_called_once()


async def test_mocker_stub(mocker):
    stub = mocker.async_stub()
    stub.assert_called_once()


async def test_fixture_mock(async_mock):
    await async_mock()
    async_mock.assert_called_once()


class TestCaseAsserts(unittest.IsolatedAsyncioTestCase):
    async def test_unittest_read(self):
        fake = AsyncMock()
        await fake()
        self.assertEqual(fake.call_count, 1)
