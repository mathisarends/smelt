from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

__all__ = ["AsyncIterator"]


@asynccontextmanager
async def manager() -> AsyncIterator[int]:
    yield 1
