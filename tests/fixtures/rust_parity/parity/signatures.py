"""Signatures that exercise counting, positions and the suggested rewrite."""

import functools
from typing import overload, override


def plain(a, b, c, d):
    return a, b, c, d


def with_defaults(a, b=1, c=(2), d=[3, 4]):
    return a, b, c, d


def annotated(a: int, b: "str", c: (int), d: dict[str, int] = {}) -> None:
    return None


def already_keyword(a, b, c, *, d, e=1):
    return a, b, c, d, e


def default_before_star(a, b, c=1, *, d):
    return a, b, c, d


def odd_star_spacing(a, b, c  ,  *  ,  d):
    return a, b, c, d


def star_with_comment(
    a,
    b,
    c,  # the separator holds a comment
    *,
    d,
):
    return a, b, c, d


def positional_only(a, b, c, /, d):
    return a, b, c, d


def boundary_positional_only(a, b, /, c, d):
    return a, b, c, d


def variadic(a, b, c, *rest):
    return a, b, c, rest


def keyword_variadic(a, b, c, **rest):
    return a, b, c, rest


def größe(länge, breite, höhe, tiefe="ü"):
    return länge, breite, höhe, tiefe


async def asynchronous(a, b, c):
    return a, b, c


@functools.cache
def decorated(a, b, c):
    return a, b, c


@functools.lru_cache(maxsize=2)
@functools.wraps(plain)
async def stacked(a, b, c):
    return a, b, c


@functools.cache  # a comment between decorator and definition

def spaced_decorator(a, b, c):
    return a, b, c


@overload
def exempt_overload(a, b, c) -> int: ...
@overload
def exempt_overload(a, b, c, d) -> str: ...
def exempt_overload(a, b, c, d=None):
    return a, b, c, d


class Methods:
    def method(self, a, b, c):
        return a, b, c

    def within_limit(self, a, b):
        return a, b

    @classmethod
    def build(cls, a, b, c):
        return a, b, c

    @staticmethod
    def static(a, b, c):
        return a, b, c

    @override
    def exempt(self, a, b, c):
        return a, b, c

    def __init__(self, a, b, c, d):
        self.values = a, b, c, d

    def __(self, a, b, c):
        return a, b, c

    def nested_function(self):
        def inner(a, b, c):
            return a, b, c

        return inner

    class Inner:
        def deep(self, a, b, c):
            return a, b, c


def no_self_in_function(self, a, b):
    return self, a, b


if plain:
    def in_if(a, b, c):
        return a, b, c
elif with_defaults:
    def in_elif(a, b, c):
        return a, b, c
else:
    class InElse:
        def method(self, a, b, c):
            return a, b, c

try:
    def in_try(a, b, c):
        return a, b, c
except ValueError:
    def in_handler(a, b, c):
        return a, b, c
else:
    def in_try_else(a, b, c):
        return a, b, c
finally:
    def in_finally(a, b, c):
        return a, b, c

try:
    pass
except* TypeError:
    def in_star_handler(a, b, c):
        return a, b, c

for _ in ():
    def in_for(a, b, c):
        return a, b, c
else:
    def in_for_else(a, b, c):
        return a, b, c

while False:
    def in_while(a, b, c):
        return a, b, c
else:
    def in_while_else(a, b, c):
        return a, b, c

with open(__file__) as handle:
    def in_with(a, b, c):
        return a, b, c

match plain:
    case 1:
        def in_case(a, b, c):
            return a, b, c
    case _:
        class InCase:
            def method(self, a, b, c):
                return a, b, c


lambda a, b, c, d: (a, b, c, d)
