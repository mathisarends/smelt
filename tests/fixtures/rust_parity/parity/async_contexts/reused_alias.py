from collections.abc import AsyncGenerator as Gen
from collections.abc import AsyncIterator  # dropped with its comment
from contextlib import asynccontextmanager


@asynccontextmanager
async def manager() -> AsyncIterator[int]:
    yield 1
