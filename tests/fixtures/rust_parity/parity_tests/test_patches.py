from unittest.mock import patch

import pytest

from parity.test_support.service import Service


def test_many_patches(monkeypatch, mocker):
    patch("parity.test_support.service.normalize")
    patch("parity.test_support.service.normalize")
    patch.object(Service, "plain")
    monkeypatch.setattr(Service, "plain", str)
    monkeypatch.delattr(Service, "plain")
    mocker.patch("parity.test_support.service.normalize")
    assert Service()


def test_one_patch(monkeypatch):
    monkeypatch.setattr(Service, "plain", str)
    assert Service()


def test_string_forms(monkeypatch):
    patch(target="parity.test_support.service.normalize")
    patch(target=Service)
    patch.dict(in_dict="os.environ")
    monkeypatch.setattr("os.sep", "/")
    monkeypatch.setattr("os.sep", "/", raising=False)
    monkeypatch.setattr(target="os.sep", value="/")
    monkeypatch.setattr("os.sep", name="sep", value="/")
    monkeypatch.delattr("os.sep")
    monkeypatch.delattr("os", "sep")
    assert Service()


class TestPatches:
    def test_in_method(self, monkeypatch):
        monkeypatch.setattr(Service, "plain", str)
        monkeypatch.setattr(Service, "fetch", str)
        assert Service()


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr("os.sep", "/")
    return Service()


def test_helper_patches():
    def install():
        patch("parity.test_support.service.normalize")
        patch("parity.test_support.service.normalize")

    install()
    assert Service()
