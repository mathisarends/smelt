import collections.abc as abc
import contextlib
import contextlib as ctx
import typing
import typing as t


@contextlib.asynccontextmanager
async def from_typing() -> typing.AsyncIterator[int]:
    yield 1


@ctx.asynccontextmanager
async def from_alias() -> t.AsyncIterator[str]:
    yield "a"


@ctx.asynccontextmanager
async def from_abc() -> abc.AsyncIterator[bytes]:
    yield b"a"


@ctx.asynccontextmanager
async def quoted() -> "abc.AsyncIterator[bytes]":
    yield b"a"
