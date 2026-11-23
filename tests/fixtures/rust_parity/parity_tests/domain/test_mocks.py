from unittest import mock
from unittest.mock import (
    AsyncMock,
    MagicMock,
    Mock,
    NonCallableMagicMock,
    NonCallableMock,
    PropertyMock,
    create_autospec,
    mock_open,
    patch,
)

import pytest

from parity.test_support.service import Service


def test_constructors():
    assert Mock() and MagicMock() and AsyncMock()
    assert NonCallableMock() and NonCallableMagicMock()
    assert PropertyMock() and mock_open() and create_autospec(Service)
    assert mock.Mock() and mock.MagicMock()


def test_patches(monkeypatch):
    with patch("parity.test_support.service.normalize"):
        assert Service()
    patch.object(Service, "plain")
    patch.dict("os.environ", {"A": "1"})
    patch.multiple(Service, plain=1)
    monkeypatch.setattr("parity.test_support.service.normalize", str)
    monkeypatch.setattr(Service, "plain", str)
    monkeypatch.delattr("parity.test_support.service.normalize")
    monkeypatch.setenv("A", "1")


def test_pytest_mock(mocker):
    mocker.patch("parity.test_support.service.normalize")
    mocker.patch.object(Service, "plain")
    mocker.spy(Service, "plain")
    mocker.stub()
    mocker.async_stub()
    mocker.Mock()
    assert mocker.MagicMock()


def test_local_context(monkeypatch):
    with monkeypatch.context() as patched:
        patched.setattr(Service, "plain", str)
    assert patched


class TestUse:
    @pytest.fixture
    def stubbed(self):
        return Mock()

    def test_fixture(self, stubbed):
        assert stubbed.called
