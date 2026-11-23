from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator; marker = 1
from contextlib import asynccontextmanager


@asynccontextmanager
async def manager() -> AsyncIterator[int]:
    yield marker
