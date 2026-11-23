from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def manager() -> "AsyncIterator[int]":
    yield 1
