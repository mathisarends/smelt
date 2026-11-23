import pytest

from parity.test_support.service import Worker


@pytest.fixture
def annotated() -> Worker:
    return Worker()
