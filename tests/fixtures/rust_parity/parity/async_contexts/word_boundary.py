from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

PLAIN = "NotAsyncGeneratorHere AsyncGenerators"


@asynccontextmanager
async def manager() -> AsyncIterator[int]:
    yield 1
