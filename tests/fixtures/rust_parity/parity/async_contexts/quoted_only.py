from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


@asynccontextmanager
async def only() -> "AsyncIterator[None]":
    yield
