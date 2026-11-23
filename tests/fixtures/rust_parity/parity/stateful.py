"""Dataclasses that write to their own fields."""

import dataclasses
from collections import Counter, OrderedDict, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, MutableMapping


@dataclass
class Assigns:
    value: int = 0

    def set(self, value):
        self.value = value

    def augmented(self):
        self.value += 1

    def annotated(self):
        self.other: int = 1

    def bare_annotation(self):
        self.declared: int

    def deleted(self):
        del self.value

    def unpacked(self):
        self.a, (self.b, *self.c) = 1, (2, 3)
        [self.d, other] = 1, 2

    def item(self):
        self.mapping["key"] = 1
        self.nested[0][1] = 2
        del self.mapping["key"]

    def through_attribute(self):
        self.nested.value = 1
        self.nested[0].value = 2

    def on_other(self, other):
        other.value = 1

    def reads(self):
        return self.value

    def __post_init__(self):
        self.value = 1

    @staticmethod
    def static(self):
        self.value = 1

    @classmethod
    def build(cls):
        cls.value = 1

    def no_receiver():
        self.value = 1

    def nested_function(self):
        def inner():
            self.from_inner = 1

        return inner

    def default_argument(self, value=setattr(self, "x", 1)):
        return value

    def renamed(this, /):
        this.value = 1


@dataclasses.dataclass(frozen=False)
class ExplicitlyMutable:
    items: list[int] = field(default_factory=list)
    table: dict[str, int] = field(default_factory=dict)
    seen: "set[str]" = field(default_factory=set)
    queue: deque[int] = field(default_factory=deque)
    counts: Counter = field(default_factory=Counter)
    ordered: OrderedDict = field(default_factory=OrderedDict)
    groups: defaultdict = field(default_factory=lambda: defaultdict(list))
    mapping: MutableMapping[str, int] = None
    by_factory: Any = field(default_factory=list)
    dotted_factory: Any = field(default_factory=dataclasses.field)
    frozen_items: tuple[int, ...] = ()
    quoted_bad: "not valid(" = None
    plain = []

    def append(self):
        self.items.append(1)

    def update(self):
        self.table.update({})

    def add(self):
        self.seen.add("x")

    def pop_left(self):
        self.queue.popleft()

    def by_factory_call(self):
        self.by_factory.extend([])

    def reads_container(self):
        return self.items.count(1)

    def non_container(self):
        self.frozen_items.append(1)
        self.plain.append(1)
        self.quoted_bad.append(1)

    def uses_setattr(self):
        setattr(self, "value", 1)

    def other_setattr(self, other):
        setattr(other, "value", 1)
        setattr(*self)
        object.__setattr__(self, "value", 1)

    def other_receiver(self, other):
        other.items.append(1)


@dataclass(frozen=True)
class Frozen:
    value: int = 0

    def set(self):
        self.value = 1


@dataclass(frozen=flag)
class UnknownFrozen:
    value: int = 0

    def set(self):
        self.value = 1


@dataclass(frozen=0)
class FalsyFrozen:
    value: int = 0

    def set(self):
        self.value = 1


@dataclass(order=True)
class WithOptions:
    value: int = 0

    def set(self):
        self.value = 1


@other
@dataclass
class SecondDecorator:
    value: int = 0

    def set(self):
        self.value = 1


@dataclass
@dataclass(frozen=True)
class FirstDecides:
    value: int = 0

    def set(self):
        self.value = 1


class Plain:
    def set(self):
        self.value = 1


def scope():
    @dataclass
    class Local:
        value: int = 0

        def set(self):
            self.value = 1

    return Local
