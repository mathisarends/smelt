from contextlib import asynccontextmanager as manager
from typing import AsyncIterator as Stream


@manager
async def aliased() -> Stream[int]:
    yield 1
