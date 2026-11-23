from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from somewhere import *


@asynccontextmanager
async def manager() -> AsyncIterator[int]:
    yield 1
