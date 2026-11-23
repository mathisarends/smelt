"""Lookup sets rebuilt inside loops."""

import typing
from dataclasses import dataclass
from dataclasses import dataclass as frozen_record
from typing import Generic, Tuple, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Item:
    """Declared fields of a local record."""

    id: str
    size: int = 0
    ratio: float = 1.5
    tags: tuple[str, ...] = ()
    quoted: "tuple[str, ...]" = ("a", "b")
    children: "tuple[Item, ...]" = ()
    computed: str = make()
    twice: str
    twice: str
    hidden: str
    plain = 1

    def hidden(self):
        return 1

    def own_tags(self, wanted: tuple[str, ...]):
        for tag in wanted:
            if tag in {other for other in self.tags}:
                return tag
        return None

    def other_receiver(this, wanted: tuple[int, ...], items: tuple["Item", ...]):
        for size in wanted:
            if size in {item.size for item in items}:
                return this.id
        return None

    @property
    def decorated(self):
        for tag in self.tags:
            if tag in {other for other in self.quoted}:
                return tag
        return None


@frozen_record(frozen=True, slots=True)
class Aliased:
    names: tuple[str, ...]

    def check(self):
        for name in self.names:
            if name in {other for other in self.names}:
                return name
        return None

    def keyword_only_receiver(*, self):
        for name in self.names:
            if name in {other for other in self.names}:
                return name
        return None


@dataclass(frozen=False)
class Thawed:
    names: tuple[str, ...]

    def check(self):
        for name in self.names:
            if name in {other for other in self.names}:
                return name
        return None


@dataclass
class Undeclared:
    names: tuple[str, ...]

    def check(self):
        for name in self.names:
            if name in {other for other in self.names}:
                return name
        return None


@dataclass(frozen=True)
class WithBase(Item):
    names: tuple[str, ...]

    def check(self):
        for name in self.names:
            if name in {other for other in self.names}:
                return name
        return None


@dataclass(frozen=True, **options)
class Unpacked:
    names: tuple[str, ...]

    def check(self):
        for name in self.names:
            if name in {other for other in self.names}:
                return name
        return None


@dataclass(frozen=True)
class CustomAccess:
    names: tuple[str, ...]

    def __getattr__(self, name):
        return name

    def check(self):
        for name in self.names:
            if name in {other for other in self.names}:
                return name
        return None


@dataclass(frozen=True)
class CustomNew:
    names: tuple[str, ...]

    def __new__(cls):
        return object.__new__(cls)

    def check(self):
        for name in self.names:
            if name in {other for other in self.names}:
                return name
        return None


@dataclass(frozen=True)
class Parametrized[V]:
    names: tuple[str, ...]

    def check(self):
        for name in self.names:
            if name in {other for other in self.names}:
                return name
        return None


@dataclass(frozen=True)
@decorator
class TwoDecorators:
    names: tuple[str, ...]

    def check(self):
        for name in self.names:
            if name in {other for other in self.names}:
                return name
        return None


@dataclass(frozen=True)
class OddBody:
    names: tuple[str, ...]
    for index in range(3):
        pass

    def check(self, wanted: tuple["OddBody", ...]):
        for one in wanted:
            if one in {other for other in wanted}:
                return one
        return None

    def field_access(self):
        for name in self.names:
            if name in {other for other in self.names}:
                return name
        return None


@dataclass(frozen=True)
class Rebound:
    names: tuple[str, ...]


Rebound = make()


class Plain:
    names: tuple[str, ...]

    def method(self, wanted: tuple[str, ...], available: tuple[str, ...]):
        for name in wanted:
            if name not in {item for item in available}:
                raise ValueError("Unknown name")

    def instance_field(self):
        for name in self.names:
            if name in {other for other in self.names}:
                return name
        return None

    @staticmethod
    def decorated(wanted: tuple[str, ...], available: tuple[str, ...]):
        for name in wanted:
            if name in {item for item in available}:
                return name
        return None

    class Nested:
        def method(self, wanted: tuple[str, ...], available: tuple[str, ...]):
            for name in wanted:
                if name in {item for item in available}:
                    return name
            return None


def plain(wanted: tuple[str, ...], available: tuple[str, ...]):
    for name in wanted:
        if name not in {item for item in available}:
            raise ValueError("Unknown name")


def membership(wanted: tuple[int, ...], available: tuple[int, ...]):
    for value in wanted:
        if value in {other for other in available}:
            return value
    return None


def scalar_annotations(
    a: tuple[bytes, ...],
    b: tuple[float, ...],
    c: tuple[bool, ...],
    d: Tuple[str, ...],
    e: typing.Tuple[int, ...],
    /,
    f: "tuple[str, ...]" = (),
    *,
    g: tuple["str", ...] = (),
):
    for one in a:
        if one in {x for x in a}:
            pass
    for two in b:
        if two in {x for x in b}:
            pass
    for three in c:
        if three in {x for x in c}:
            pass
    for four in d:
        if four in {x for x in d}:
            pass
    for five in e:
        if five in {x for x in e}:
            pass
    for six in f:
        if six in {x for x in f}:
            pass
    for seven in g:
        if seven in {x for x in g}:
            pass


def unknown_annotations(
    a: list[str],
    b: tuple[str],
    c: tuple[str, int],
    d: tuple[object, ...],
    e: "not valid (",
    f: " tuple[str, ...]",
    g: "'tuple[str, ...]'",
    h: complex,
    i: tuple[complex, ...],
    j,
):
    for one in a:
        if one in {x for x in a}:
            pass
    for two in b:
        if two in {x for x in b}:
            pass
    for three in c:
        if three in {x for x in c}:
            pass
    for four in d:
        if four in {x for x in d}:
            pass
    for five in e:
        if five in {x for x in e}:
            pass
    for six in f:
        if six in {x for x in f}:
            pass
    for seven in g:
        if seven in {x for x in g}:
            pass
    for eight in i:
        if eight in {x for x in i}:
            pass
    for nine in j:
        if nine in {x for x in j}:
            pass
    return h


def grouped_renames(wanted: tuple[str, ...], available: tuple[str, ...]):
    for name in wanted:
        if name in {a for a in available}:
            continue
        if name in {b for b in available}:
            break
        found = name not in {c for c in available}
        other: bool = name in {d for d in available}
        return found and other


def separate_projections(wanted: tuple[Item, ...], available: tuple[Item, ...]):
    for one in wanted:
        if one.id in {item.id for item in available}:
            pass
        if one.size in {item.size for item in available}:
            pass
        if one.id in {other.id for other in available}:
            pass
        if one.id in {item.id for item in wanted}:
            pass


def projections(wanted: tuple[str, ...], rows: tuple[tuple[str, ...], ...], values: tuple[int, ...]):
    for name in wanted:
        if name in {row[0] for row in rows}:
            pass
        if name in {row[True] for row in rows}:
            pass
        if name in {name for item in wanted}:
            pass
        if name in {"fixed" for item in wanted}:
            pass
        if name in {item + "suffix" for item in wanted}:
            pass
        if name in {+item for item in wanted}:
            pass
        if name in {not item for item in wanted}:
            pass
        if name in {item == "a" for item in wanted}:
            pass
        if name in {item in wanted for item in wanted}:
            pass
        if name in {item and "a" for item in wanted}:
            pass
        if name in {(item or "a") and "b" for item in wanted}:
            pass
        if name in {item if item else "a" for item in wanted}:
            pass
        if name in {(item, item) for item in wanted}:
            pass
        if name in {f"{item}" for item in wanted}:
            pass
        if name in {len(item) for item in wanted}:
            pass
        if name in {item.upper for item in wanted}:
            pass
        if name in {1j for item in wanted}:
            pass
        if name in {None for item in wanted}:
            pass
        if name in {b"x" for item in wanted}:
            pass
        if name in {1.5 for item in wanted}:
            pass
        if name in {True for item in wanted}:
            pass
        if name in {... for item in wanted}:
            pass
        if name in {u"kind" for item in wanted}:
            pass
    for value in values:
        if value in {item * 2 for item in values}:
            pass
        if value in {-item for item in values}:
            pass
        if value in {item + 1.5 for item in values}:
            pass
        if value in {item << 1 for item in values}:
            pass


def comprehension_shapes(wanted: tuple[str, ...], rows: tuple[tuple[str, ...], ...], flag: bool):
    for name in wanted:
        if name in {item for item in wanted if item}:
            pass
    for name in wanted:
        if name in {item for row in rows for item in row}:
            pass
    for name in wanted:
        if name in {a for a, b in rows}:
            pass
    for name in wanted:
        if name in {item for item in [name]}:
            pass
    for name in wanted:
        if name in {*wanted}:
            pass
    for name in wanted:
        if name in {"a", "b"}:
            pass
    for name in wanted:
        if name in [item for item in wanted]:
            pass
    for name in wanted:
        if {item for item in wanted} in name:
            pass
    for name in wanted:
        if name == {item for item in wanted}:
            pass
    for name in wanted:
        if name in {item for item in wanted} and flag:
            pass
    for name in wanted:
        if flag or name not in {item for item in wanted}:
            pass
    for name in wanted:
        if "a" < name in {item for item in wanted}:
            pass
    for name in wanted:
        if name in {item for item in wanted} in {other for other in wanted}:
            pass
    for name in wanted:
        if name in {item in {x for x in wanted} for item in wanted}:
            pass
    for name in wanted:
        if name in {item in {x for x in (item, item)} for item in wanted}:
            pass


async def asynchronous(wanted: tuple[str, ...], available: tuple[str, ...]):
    for name in wanted:
        if name in {item for item in available}:
            return name
    return None


async def awaiting(wanted: tuple[str, ...], available: tuple[str, ...]):
    await pause()
    for name in wanted:
        if name in {item for item in available}:
            return name
    return None


async def async_loop(wanted: tuple[str, ...], available: tuple[str, ...]):
    async for name in wanted:
        if name in {item for item in available}:
            return name
    return None


async def async_comprehension(wanted: tuple[str, ...], available: tuple[str, ...]):
    for name in wanted:
        if name in {item async for item in available}:
            return name
    return None


def yielding(wanted: tuple[str, ...], available: tuple[str, ...]):
    for name in wanted:
        if name in {item for item in available}:
            yield name


def delegating(wanted: tuple[str, ...], available: tuple[str, ...]):
    yield from ()
    for name in wanted:
        if name in {item for item in available}:
            return name
    return None


def nested_yield(wanted: tuple[str, ...], available: tuple[str, ...]):
    def inner():
        yield 1

    for name in wanted:
        if name in {item for item in available}:
            return name
    return inner


def lambda_yield(wanted: tuple[str, ...], available: tuple[str, ...], run=lambda: (yield)):
    for name in wanted:
        if name in {item for item in available}:
            return name
    return run


@decorator
def decorated(wanted: tuple[str, ...], available: tuple[str, ...]):
    for name in wanted:
        if name in {item for item in available}:
            return name
    return None


def inner_scope():
    def nested(wanted: tuple[str, ...], available: tuple[str, ...]):
        for name in wanted:
            if name in {item for item in available}:
                return name
        return None

    return nested


def conditional_branches(wanted: tuple[str, ...], available: tuple[str, ...], flag: bool):
    for name in wanted:
        if flag:
            kept = name
        elif name:
            kept = name
        else:
            kept = name
        if kept in {item for item in available}:
            pass
    for name in wanted:
        if flag:
            only = name
        if only in {item for item in available}:
            pass
    for name in wanted:
        if flag:
            mixed = name
        else:
            mixed = 1
        if mixed in {item for item in available}:
            pass
    for name in wanted:
        if flag:
            pass
        elif name in {item for item in available}:
            pass
        elif name not in {other for other in available}:
            pass
        else:
            result = name in {third for third in available}
    for name in wanted:
        if flag:
            print(name)
        elif name in {item for item in available}:
            pass
    for name in wanted:
        if name in {item for item in available}:
            pass
        elif print(name):
            pass
    for name in wanted:
        if name in {item for item in available}:
            pass
        else:
            print(name)


def statements_in_loop(wanted: tuple[str, ...], available: tuple[str, ...], flag: bool):
    for name in wanted:
        print(name)
        if name in {item for item in available}:
            pass
    for name in wanted:
        with flag:
            pass
        if name in {item for item in available}:
            pass
    for name in wanted:
        for other in wanted:
            pass
        if name in {item for item in available}:
            pass
    for name in wanted:
        while flag:
            pass
        if name in {item for item in available}:
            pass
    for name in wanted:
        try:
            pass
        except ValueError:
            pass
        if name in {item for item in available}:
            pass
    for name in wanted:
        assert name
        if name in {item for item in available}:
            pass
    for name in wanted:
        del flag
        if name in {item for item in available}:
            pass
    for name in wanted:
        name
        if name in {item for item in available}:
            pass
    for name in wanted:
        global shared
        if name in {item for item in available}:
            pass
    for name in wanted:
        import os
        if name in {item for item in available}:
            pass
    for name in wanted:
        def local():
            pass
        if name in {item for item in available}:
            pass
    for name in wanted:
        class Local:
            pass
        if name in {item for item in available}:
            pass
    for name in wanted:
        total: int
        if name in {item for item in available}:
            pass
    for name in wanted:
        total += 1
        if name in {item for item in available}:
            pass
    for name in wanted:
        match name:
            case _:
                pass
        if name in {item for item in available}:
            pass


def raises(wanted: tuple[str, ...], available: tuple[str, ...]):
    for name in wanted:
        if name in {a for a in available}:
            raise TypeError("literal", 1, None)
    for name in wanted:
        if name in {b for b in available}:
            raise RuntimeError
    for name in wanted:
        if name in {c for c in available}:
            raise
    for name in wanted:
        if name in {d for d in available}:
            raise ValueError("x") from None
    for name in wanted:
        if name in {e for e in available}:
            raise ValueError(name)
    for name in wanted:
        if name in {f for f in available}:
            raise ValueError(f"{name}")
    for name in wanted:
        if name in {g for g in available}:
            raise ValueError("x", key=1)
    for name in wanted:
        if name in {h for h in available}:
            raise ValueError(*"x")
    for name in wanted:
        if name in {i for i in available}:
            raise KeyError("x")
    for name in wanted:
        if name in {j for j in available}:
            raise typing.Error("x")
    for name in wanted:
        if name in {k for k in available}:
            raise ValueError()


def returns(wanted: tuple[str, ...], available: tuple[str, ...]):
    for name in wanted:
        if name in {a for a in available}:
            return
    for name in wanted:
        return name in {b for b in available}
    for name in wanted:
        return print(name in {c for c in available})


def assignments(wanted: tuple[str, ...], available: tuple[str, ...]):
    for name in wanted:
        a = name
        if a in {item for item in available}:
            pass
    for name in wanted:
        b: str = name
        if b in {item for item in available}:
            pass
    for name in wanted:
        c: str
        if c in {item for item in available}:
            pass
    for name in wanted:
        d = e = name
        if d in {item for item in available}:
            pass
    for name in wanted:
        f, g = name, name
        if f in {item for item in available}:
            pass
    for name in wanted:
        name.attr = 1
        if name in {item for item in available}:
            pass
    for name in wanted:
        h = print(name)
        if name in {item for item in available}:
            pass
    for name in wanted:
        i = name
        i = print(name)
        if i in {item for item in available}:
            pass
    for name in wanted:
        j = {item for item in available}
        k = name in j
        if k:
            pass


def written_inputs(wanted: tuple[str, ...], available: tuple[str, ...], other: tuple[str, ...]):
    for name in wanted:
        if name in {item for item in available}:
            available = other
    for name in wanted:
        kept = other
        if name in {item for item in kept}:
            pass
    for name in wanted:
        if name in {item for item in other}:
            pass
    else:
        other = wanted
    for name in wanted:
        if name in {name for item in wanted}:
            pass
    for name in wanted:
        if name in {item + name for item in wanted}:
            pass
    for name in wanted:
        limit = "a"
        if name in {item + limit for item in wanted}:
            pass


def env_before_loop(wanted: tuple[str, ...], flag: bool):
    pool = ("a", "b")
    for name in wanted:
        if name in {item for item in pool}:
            pass
    pool = ("a", 1)
    for name in wanted:
        if name in {item for item in pool}:
            pass
    pool = ()
    for name in wanted:
        if name in {item for item in pool}:
            pass
    pool: tuple[str, ...] = ("c", "d")
    for name in pool:
        if name in {item for item in pool}:
            pass
    pool, extra = ("a",), ("b",)
    for name in wanted:
        if name in {item for item in pool}:
            pass
    second = third = ("a", "b")
    for name in wanted:
        if name in {item for item in second}:
            pass
    fresh = ("x", "y")
    if flag:
        fresh = print()
    for name in wanted:
        if name in {item for item in fresh}:
            pass
    stable = ("x", "y")
    if flag:
        print()
    for name in wanted:
        if name in {item for item in stable}:
            pass
    consumed = ("x", "y")
    for consumed in wanted:
        pass
    for name in wanted:
        if name in {item for item in consumed}:
            pass
    mutated = ("x", "y")
    mutated.attr = 1
    for name in wanted:
        if name in {item for item in mutated}:
            pass
    indexed = ("x", "y")
    indexed[0] = 1
    for name in wanted:
        if name in {item for item in indexed}:
            pass
    removed = ("x", "y")
    del removed
    for name in wanted:
        if name in {item for item in removed}:
            pass
    deep = ("x", "y")
    deep.a[0].b = 1
    for name in wanted:
        if name in {item for item in deep}:
            pass
    call_root = ("x", "y")
    make(call_root).a = 1
    for name in wanted:
        if name in {item for item in call_root}:
            pass
    walrused = ("x", "y")
    print(walrused := 1)
    for name in wanted:
        if name in {item for item in walrused}:
            pass
    handler = ("x", "y")
    try:
        pass
    except ValueError as handler:
        pass
    for name in wanted:
        if name in {item for item in handler}:
            pass
    aliased = ("x", "y")
    type aliased = int
    for name in wanted:
        if name in {item for item in aliased}:
            pass
    function_name = ("x", "y")

    def function_name():
        pass

    for name in wanted:
        if name in {item for item in function_name}:
            pass


def loop_shapes(rows: tuple[tuple[str, ...], ...], items: tuple[Item, ...], flag: bool):
    for name in rows[0]:
        if name in {item for item in rows[0]}:
            pass
    for name in ("a", "b"):
        if name in {item for item in ("a", "b")}:
            pass
    for name in ("a", 1):
        if name in {item for item in ("a", "b")}:
            pass
    for one, two in rows:
        if one in {item for item in rows[0]}:
            pass
    for name in rows[flag]:
        if name in {item for item in rows[0]}:
            pass
    for name in rows[-1]:
        if name in {item for item in rows[0]}:
            pass
    for name in rows[1:]:
        if name in {item for item in rows[0]}:
            pass
    for item in items:
        for child in item.children:
            if child.id in {other.id for other in items}:
                pass
    for item in items:
        if item.id in {tag for tag in item.tags}:
            pass
    for item in items:
        if item.id in {tag for tag in item.quoted}:
            pass
    for item in items:
        if item.ratio in {other.ratio for other in items}:
            pass
    for item in items:
        if item.computed in {other.computed for other in items}:
            pass
    for item in items:
        if item.twice in {other.twice for other in items}:
            pass
    for item in items:
        if item.hidden in {other.hidden for other in items}:
            pass
    for item in items:
        if item.plain in {other.plain for other in items}:
            pass
    for item in items:
        if item.missing in {other.missing for other in items}:
            pass
    while flag:
        for name in rows[0]:
            if name in {item for item in rows[0]}:
                pass


def not_a_generic(values: Generic[T]):
    for value in values:
        if value in {item for item in values}:
            pass


lambda_scope = lambda wanted: [
    name for name in wanted if name in {item for item in wanted}
]
