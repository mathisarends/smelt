from collections.abc import AsyncIterator, Iterator
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest
import pytest_asyncio

from parity.test_support import PublicWorker, Service
from parity.test_support.service import Worker


@pytest.fixture
def annotated() -> Service:
    return make_service()


@pytest.fixture
def quoted() -> "Service":
    return make_service()


@pytest.fixture
def inferred():
    return Service()


@pytest.fixture
def yielded():
    value = Service()
    yield value


@pytest.fixture
def iterated() -> Iterator[Worker]:
    yield Worker()


@pytest.fixture
def generated() -> Generator[Service, None, None]:
    yield Service()


@pytest_asyncio.fixture
async def async_iterated() -> AsyncIterator[Service]:
    yield Service()


@pytest.fixture
def paired() -> tuple[Service, Worker]:
    return Service(), Worker()


@pytest.fixture
def inferred_pair():
    return Service(), PublicWorker()


@pytest.fixture
def conflicting(flag):
    if flag:
        return Service()
    return Worker()


@pytest.fixture
def unfinished(flag):
    if flag:
        return Service()


@pytest.fixture(name="renamed")
def _renamed_fixture() -> Service:
    return Service()


@pytest.fixture
def async_mock():
    return AsyncMock()


@pytest.fixture
def spec_mock():
    return MagicMock(spec=Service)


@pytest.fixture
def autospec():
    return create_autospec(Service)


@pytest.fixture
def typed_mock() -> MagicMock:
    return MagicMock(spec=Service)


@pytest.fixture
def items():
    return [1, 2]


@pytest.fixture
def delegated():
    yield from [Service()]


def make_service() -> Service:
    return Service()
