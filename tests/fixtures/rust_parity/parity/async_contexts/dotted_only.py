import collections.abc.AsyncIterator as Stream
import contextlib as ctx


@ctx.asynccontextmanager
async def manager() -> Stream[int]:
    yield 1
