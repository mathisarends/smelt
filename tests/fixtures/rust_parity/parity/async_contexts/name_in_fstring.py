from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

LABEL = f"{1:AsyncGenerator}"
PLAIN = "NotAsyncGeneratorHere"


@asynccontextmanager
async def manager() -> AsyncIterator[int]:
    yield 1
