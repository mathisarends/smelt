from collections.abc import (
    AsyncGenerator,  # kept
    AsyncIterator,
)
from contextlib import asynccontextmanager


@asynccontextmanager
async def manager() -> AsyncIterator[int]:
    yield 1
