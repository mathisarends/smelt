from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


@asynccontextmanager
async def double() -> "AsyncIterator[None]":
    yield


@asynccontextmanager
async def single() -> 'AsyncIterator[int]':
    yield 1


@asynccontextmanager
async def triple() -> """AsyncIterator[str]""":
    yield "a"


@asynccontextmanager
async def raw() -> r"AsyncIterator[bytes]":
    yield b"a"


@asynccontextmanager
async def spaced() -> "AsyncIterator[int] ":
    yield 1


@asynccontextmanager
async def indented() -> " AsyncIterator[int]":
    yield 1


@asynccontextmanager
async def broken() -> "AsyncIterator[":
    yield 1
