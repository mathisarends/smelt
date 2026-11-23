from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

AsyncIterator = list


@asynccontextmanager
async def manager() -> AsyncIterator[int]:
    yield 1
