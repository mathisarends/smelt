"""A dotted import cannot be renamed, so a new import joins it."""
import collections.abc.AsyncIterator as Stream
from contextlib import asynccontextmanager
VALUE = 1


@asynccontextmanager
async def manager() -> Stream[int]:
    yield VALUE
