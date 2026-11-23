import pytest

from parity.test_support.service import Worker


@pytest.fixture
def only_below_prefix() -> Worker:
    return Worker()


def test_named_like_a_conftest(only_below_prefix):
    only_below_prefix._step()
