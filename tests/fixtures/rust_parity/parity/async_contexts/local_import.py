from contextlib import asynccontextmanager


def scope():
    from collections.abc import AsyncIterator

    @asynccontextmanager
    async def manager() -> AsyncIterator[int]:
        yield 1

    return manager
