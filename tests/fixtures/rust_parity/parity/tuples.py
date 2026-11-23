"""TYP007: fixed-length tuple types."""

import typing
import typing as t
from typing import Tuple, TypeAlias, overload, override

import typing_extensions

three: tuple[int, str, bytes]
repeated: tuple[int, int]
two: tuple[int, str]
variadic: tuple[int, ...]
empty: tuple[()]
single: tuple[int]
nested: dict[str, tuple[int, int, str]]
inner: tuple[tuple[int, int], str]
capital: Tuple[int, int]
qualified: typing.Tuple[int, str, bytes]
extension: typing_extensions.Tuple[int, int]
aliased: t.Tuple[int, int]
quoted: "tuple[int, int]"
parenthesized: tuple[(int, int)]
starred: tuple[*Ts, int, int]
literals: tuple[1, 1.0, "a", b"b", None, True]
repeated_spelling: tuple[list[int], list[int], typing.Any]
unicode: tuple[größe, größe]
assigned: tuple[int, int] = (1, 2)
self_attribute = None

Alias: TypeAlias = tuple[int, int]
QualifiedAlias: typing.TypeAlias = tuple[str, str, str]
NotAlias: int = tuple[int, int]
Plain = tuple[int, int]
type Modern = tuple[int, int, int]
type Generic[T] = tuple[T, T]


def signature(
    a: tuple[int, int],
    /,
    b: tuple[str, str] = ("", ""),
    *c: tuple[bytes, bytes],
    d: tuple[float, float],
    **e: tuple[bool, bool],
) -> tuple[int, str, bytes]:
    local: tuple[int, int] = (1, 2)
    return local


def __dunder__(a: tuple[int, int]) -> tuple[int, int]:
    local: tuple[str, str] = ("", "")
    return local


@overload
def overloaded(a: tuple[int, int]) -> None: ...


@typing.overload
def overloaded(a: tuple[str, str]) -> None: ...


def overloaded(a: tuple[bytes, bytes]) -> None:
    return None


@staticmethod
def other_decorator(a: tuple[int, int]) -> None:
    return None


@typing.no_type_check()
def called_decorator(a: tuple[int, int]) -> None:
    return None


class Holder:
    field: tuple[int, int]

    def __init__(self, a: tuple[int, int]) -> None:
        self.value: tuple[str, str] = ("", "")

    @override
    def method(self, a: tuple[int, int]) -> tuple[int, int]:
        return a

    async def coroutine(self, a: tuple[int, int, int]) -> None:
        return None

    def lambda_default(self, a=lambda: 1) -> list[tuple[int, int]]:
        return []
