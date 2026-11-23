import pytest

from parity.test_support.service import Worker


@pytest.fixture
def annotated() -> Worker:
    return Worker()


@pytest.fixture
def wraps_parent(yielded):
    return yielded


class TestScoped:
    @pytest.fixture
    def scoped(self) -> Worker:
        return Worker()
