"""VIS007: thirteen private definitions, with what does and does not count."""

import os as _os

_LIMIT = 1
_ANNOTATED: int = 2
_DECLARED: int
_lower = 3
_MIXED_case = 4
_A, _B = 5, 6
_C = _D = 7
__DUNDER__ = 8
_ÄPFEL = 9
_ǅ = 10
_123 = 11


def wrap(function):
    return function


@wrap
@wrap
class _First:
    pass


class _Second:
    def _method(self):
        return 1


class _Second:
    pass


class __dunder__:
    pass


class Public:
    pass


@wrap
def _third():
    return 1


async def _fourth():
    return 2


def _overloaded(value: int): ...


def _overloaded(value: str): ...


def __special__():
    return 3


type _Fifth = int
type _Sixth[T] = list[T]
_NotModern = int


def _seventh():
    def _nested():
        return 1

    return _nested


class _Eighth:
    pass


def _ninth():
    return 1


class _Tenth:
    pass


def _eleventh():
    return 1


class _Twelfth:
    pass


def _thirteenth():
    return 1


if _LIMIT:
    def _conditional():
        return 1


try:
    class _Guarded:
        pass
except ValueError:
    pass
