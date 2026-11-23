"""Truthiness fallbacks on optional parameters."""

import typing
import typing as t
from typing import Annotated, Optional, Union

import typing_extensions as te


def binary(options: int | None = None):
    return options or 1


def reversed_union(options: None | int = None):
    return options or 2


def nested_union(options: int | str | None = None):
    return options or 3


def nested_right(options: None | (int | str) = None):
    return options or 4


def optional(options: Optional[int] = None):
    return options or 5


def qualified(a: typing.Optional[int] = None, b: t.Optional[int] = None, c: te.Optional[int] = None):
    return (a or 1), (b or 2), (c or 3)


def unions(a: Union[int, None] = None, b: typing.Union[None, str] = None, c: Union[int, str] = None):
    return (a or 1), (b or 2), (c or 3)


def annotated(a: Annotated[int | None, "meta"] = None, b: Annotated[int, None] = None):
    return (a or 1), (b or 2)


def quoted(a: "int | None" = None, b: "Optional[int]" = None, c: " int | None" = None, d: "broken[" = None):
    return (a or 1), (b or 2), (c or 3), (d or 4)


def unqualified(a: Optional = None, b: Other[int] = None, c: list[int] = None):
    return (a or 1), (b or 2), (c or 3)


def defaults(a: int | None = 1, b: int | None = ..., c: int | None = None, d=None, *e: int | None, f: int | None = None, g: int | None):
    return (a or 1), (b or 2), (c or 3), (d or 4), (e or 5), (f or 6), (g or 7)


def positional_only(a: int | None = None, /, b: int | None = None):
    return (a or 1), (b or 2)


def shapes(value: int | None = None, other: int | None = None):
    a = value or 1
    b = value or other or 2
    c = other and value or 3
    d = (value) or 4
    e = value or (5)
    f = value or (yield)
    g = 1 or value
    h = [value or 6]
    i = {"key": value or 7}
    j = call(value or 8, key=value or 9)
    k = value or call(10)
    l = value or [
        11,
    ]
    m = (value
         or 12)
    n = value or "text # not a comment"
    o = value or "back\\slash"
    p = (value or  # comment
         13)
    q = value or (14, 15)
    r = value or (x for x in ())
    s = value or (lambda: 16)
    t = value or (other := 17)
    return a, b, c, d, e, f, g, h, i, j, k, l, m, n, o, p, q, r, s, t


def strings(value: int | None = None):
    a = f"{value or 1}"
    b = f"{value or 1=}"
    c = f"{value or 1!r}"
    d = f"{value or 1:>10}"
    e = f"{2:{value or 1}}"
    f = f"{2=:{value or 1}}"
    g = f"{2:{value or 1=}}"
    h = t"{value or 1}"
    i = t"{2:{value or 1}}"
    j = t"{2:{value or 1=}}"
    k = f"{call(value or 1)=}"
    l = t"{f'{value or 1}'}"
    m = f"{f'{value or 1}'=}"
    n = "prefix" f"{value or 1}" "suffix"
    return a, b, c, d, e, f, g, h, i, j, k, l, m, n


def skipped_contexts(value: int | None = None, flag=True):
    if value or 1:
        pass
    elif value or 2:
        pass
    while value or 3:
        break
    a = 1 if value or 4 else 2
    b = not (value or 5)
    c = flag and (value or 6)
    d = bool(value or 7)
    assert value or 8, value or 9
    e = [x for x in () if value or 10]
    f = [x for x in value or ()]
    g = [value or 11 for x in ()]
    h = (value or 12 for x in ())
    i = lambda: value or 13
    match value or 14:
        case 1 if value or 15:
            j = value or 16
    return a, b, c, d, e, f, g, h, i, j


def included_contexts(value: int | None = None, flag=True):
    if flag:
        a = value or 1
    elif flag:
        a = value or 2
    else:
        a = value or 3
    while flag:
        a = value or 4
    else:
        a = value or 5
    for item in value or ():
        a = value or 6
    else:
        a = value or 7
    try:
        a = value or 8
    except (value or Exception):
        a = value or 9
    else:
        a = value or 10
    finally:
        a = value or 11
    with value or open("x") as handle:
        a = value or 12
    b = (value or 13) if flag else (value or 14)
    del a
    return b, handle


def rebound_assignment(value: int | None = None):
    value = 1
    return value or 2


def rebound_augmented(value: int | None = None):
    value += 1
    return value or 2


def rebound_for(value: int | None = None):
    for value in ():
        pass
    return value or 2


def rebound_comprehension(value: int | None = None):
    items = [value for value in ()]
    return value or items


def rebound_walrus(value: int | None = None):
    print(value := 1)
    return value or 2


def rebound_with(value: int | None = None):
    with open("x") as value:
        pass
    return value or 2


def rebound_delete(value: int | None = None):
    del value
    return value or 2


def rebound_import(value: int | None = None, other: int | None = None, third: int | None = None):
    import value
    from os import other
    import third.part
    return (value or 1), (other or 2), (third or 3)


def rebound_function(value: int | None = None, other: int | None = None):
    def value():
        pass

    class other:
        pass

    return (value or 1), (other or 2)


def rebound_handler(value: int | None = None):
    try:
        pass
    except ValueError as value:
        pass
    return value or 2


def rebound_match(a: int | None = None, b: int | None = None, c: int | None = None):
    match 1:
        case [*a]:
            pass
        case {"k": 1, **b}:
            pass
        case 2 as c:
            pass
    return (a or 1), (b or 2), (c or 3)


def attribute_write(value: int | None = None):
    value.attr = 1
    value[0] = 2
    return value or 3


def nested_definitions(value: int | None = None):
    def inner():
        value = 1
        return value or 2

    class Inner:
        value = 2

        def method(self, value: int | None = None):
            return value or 3

    return (value or 4), inner, Inner, lambda value=1: value or 5


class Owner:
    def method(self, value: str | None = None):
        return value or ""

    class Nested:
        async def method(self, value: str | None = None):
            def deeper(other: str | None = None):
                return other or value

            return value or deeper
