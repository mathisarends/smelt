from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional


@asynccontextmanager
def synchronous() -> AsyncIterator[int]:
    yield 1


@asynccontextmanager
async def bare() -> AsyncIterator:
    yield 1


@asynccontextmanager
async def two_arguments() -> AsyncIterator[int, str]:
    yield 1


@asynccontextmanager
async def wrapped() -> Optional[AsyncIterator[int]]:
    yield 1


@asynccontextmanager
async def unannotated():
    yield 1


@other
async def other_decorator() -> AsyncIterator[int]:
    yield 1


@asynccontextmanager()
async def called() -> AsyncIterator[int]:
    yield 1


class Holder:
    @staticmethod
    @asynccontextmanager
    async def method() -> AsyncIterator[
        int
    ]:
        yield 1

    @asynccontextmanager
    async def sliced(self) -> AsyncIterator[1:2]:
        yield 1
