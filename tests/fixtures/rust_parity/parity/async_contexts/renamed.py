"""The only use of AsyncIterator moves to AsyncGenerator."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


@asynccontextmanager
async def exclusive() -> AsyncIterator[None]:
    yield
