"""Mentions AsyncGenerator, so the name is not free."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


@asynccontextmanager
async def manager() -> AsyncIterator[int]:
    yield 1
