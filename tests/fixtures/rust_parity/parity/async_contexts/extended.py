from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager


@asynccontextmanager
async def first() -> AsyncIterator[int]:
    yield 1


@asynccontextmanager
async def second() -> AsyncIterator[tuple[str, int]]:
    yield "a", 1


async def stream(items: Sequence[int]) -> AsyncIterator[int]:
    yield items[0]
