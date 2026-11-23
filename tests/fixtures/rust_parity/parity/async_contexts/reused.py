from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager


@asynccontextmanager
async def manager() -> AsyncIterator[int]:
    yield 1


def other() -> AsyncGenerator[int, None]:
    raise NotImplementedError
