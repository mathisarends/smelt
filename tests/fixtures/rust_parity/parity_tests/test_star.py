from parity.test_support.service import Service
from os.path import *


def local_helper():
    assert True is not None


def test_star_forgets_names():
    made = Service()
    assert made._state
    local_helper()


def _dynamic():
    global replaced
    replaced = 1


def replaced():
    assert True is not None


def test_dynamic_helper():
    replaced()


@staticmethod
def decorated():
    assert True is not None


def test_decorated_helper():
    decorated()


def twice():
    assert True is not None


def twice():
    return None


def test_ambiguous_helper():
    twice()


def generator():
    assert True is not None
    yield


def test_generator_helper():
    generator()


def test_nested_helper():
    def check():
        assert True is not None

    check()
