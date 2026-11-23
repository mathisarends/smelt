"""CPX007: every decision kind, order and digest."""

from dataclasses import dataclass


def wrap(function):
    return function


class Guarded:
    def __init__(self, value):
        if not value:
            raise ValueError("missing")
        self.value = value


class AllKinds:
    def __init__(self, values, stream=None):
        if values:
            self.a = 1
        elif stream:
            self.a = 2
        elif values is None:
            self.a = 3
        else:
            if stream is None:
                self.a = 4
        for value in values:
            if value:
                break
        else:
            self.b = 0
        while values:
            values = values[1:]
        try:
            self.c = 1 / len(values)
        except ZeroDivisionError as error:
            raise ValueError("empty") from error
        else:
            self.d = 1
        finally:
            self.e = 2
        try:
            self.f = 0
        except* ValueError:
            self.f = 1
        match values:
            case [first, *rest] if first:
                self.g = rest
            case {"key": value, **others}:
                self.g = others
            case str() | bytes():
                self.g = None
            case _:
                self.g = 0
        self.h = [item for item in values]
        self.i = {item for item in values if item}
        self.j = {item: item for item in values}
        self.k = sum(item for item in values)
        self.l = (item for item in values)
        self.m = 1 if values else 2
        self.n = (item for item in values) if values else None
        self.o = lambda: 1 if values else 2

        def nested():
            if values:
                raise ValueError

        class Local:
            attribute = 1 if values else 2

        self.p = nested, Local


class Asynchronous:
    async def __init__(self, stream):
        async for item in stream:
            self.item = item
        async with stream as handle:
            self.handle = [item async for item in handle]
        self.ready = True if stream else False


@dataclass
class PostInit:
    value: int

    def __post_init__(self):
        if self.value < 0:
            raise ValueError("négatif")
        self.value = abs(self.value) if self.value else 1


class Decorated:
    @wrap
    @wrap
    def __init__(self, value):
        self.a = 1 if value else 2
        self.b = 3 if value else 4
        self.c = 5 if value else 6


class Ignored:
    def __new__(cls, value):
        if value:
            raise ValueError
        return super().__new__(cls) if value else None

    def setup(self, value):
        if value:
            raise ValueError
        return 1 if value else 2


def outer():
    class Nested:
        def __init__(self, value):
            ä = "ü"; self.a = [v for v in value] if value else ä
            if value: raise ValueError(f"{value!r:>{len(value)}} {value=}")

    return Nested


class Literals:
    def __init__(self, value):
        if value == u"text" "more":
            self.a = (b"bytes", 1.5, 2j, 1e400, 10**100, 0xFF, None, True, ..., -1)
        if value in {1: "a", **{}}:
            self.b = value[1:2, ::3]
        if not (value := self.compute()):
            del self.b
        assert value, "message"
        global state
        import math as m, os
        from . import sibling
        from ..parity import frozen as f
        self.c = f"plain" u"tail" f"{value}" "" f"{{}}"
        self.d = await_nothing = lambda a, /, b=1, *c, d, e=2, **f: (a, b, c, d, e, f)
        self.e: "int" = 1
        self.f, *self.g = value
        value += 1
        print(*value, key=1, **value)
