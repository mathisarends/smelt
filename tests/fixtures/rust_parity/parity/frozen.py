"""MOD011: direct mutable fields of explicitly frozen dataclasses."""

import dataclasses
import dataclasses as dc
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import dataclass as frozen_model


@dataclass(frozen=True)
class Direct:
    items: list[int]
    mapping: "dict[str, int]"
    tags: set[str] = dataclasses.field(default_factory=set)
    bare: list
    read_only: Mapping[str, int]
    broken: "not valid ("
    union: "list[int] | None"
    indented: " list[int]"
    implicit: "li" "st[int]"
    wrapped: tuple[list[int], ...]
    größe: dict[str, int]
    Direct.attribute: list[int]


@dc.dataclass(order=True, frozen=True)
class ThroughModule:
    values: list[str]


@frozen_model(frozen=True)
class ThroughAlias:
    values: set[str]


@dataclasses.dataclass(frozen=False)
class NotFrozen:
    values: list[str]


@dataclass(frozen=1)
class TruthyOnly:
    values: list[str]


options = {"frozen": True}


@dataclass(frozen=True, **options)
class Unpacked:
    values: list[str]


@dataclass
class Plain:
    values: list[str]


@staticmethod
@dataclass(frozen=True)
class Decorated:
    values: dict[str, str]


def factory():
    @dataclass(frozen=True)
    class Local:
        values: list[int]

    class Outer:
        @dataclass(frozen=True)
        class Inner:
            values: set[int]

    return Local, Outer
