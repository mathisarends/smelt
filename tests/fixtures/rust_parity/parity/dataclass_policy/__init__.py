"""Dataclasses under a package that requires options."""

from __future__ import annotations

import abc
import collections.abc as container
import dataclasses
import dataclasses as dc
import external_pkg
import parity.modeling.base as project_base
from abc import ABC
from dataclasses import dataclass, field
from functools import cached_property
from typing import Generic, NamedTuple, Protocol

from external import ThirdParty
from parity.modeling.base import Options
from typing_extensions import TypedDict

from .relative import Sibling

Renamed = ThirdParty


@dataclass
class Bare:
    value: int


@dataclass()
class Empty:
    value: int


@dataclass(order=True)
class OtherOption:
    value: int


@dataclass(frozen=False, slots=False, kw_only=False)
class AllFalse:
    value: int


@dataclass(frozen=True)
class OnlyFrozen:
    value: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Complete:
    value: int


@dataclass(frozen=FLAG, slots=OTHER, kw_only=True)
class NotJudged:
    value: int


@dataclass(frozen=False, order=True)
class MixedFalse:
    value: int


@dataclass(**options)
class Unpacked:
    value: int


@dataclass(frozen=True, **options)
class UnpackedAfter:
    value: int


@dataclass("positional")
class Positional:
    value: int


@dataclass(item for item in ())
class BareGenerator:
    value: int


@dataclasses.dataclass
class Qualified:
    value: int


@dc.dataclass(frozen=True)
class QualifiedCall:
    value: int


@other.dataclass
class OtherOwner:
    value: int


@(dataclass)
class Parenthesized:
    value: int


@(dataclass())
class ParenthesizedCall:
    value: int


@dataclass(  # keep
    frozen=True,
)
class MultiLine:
    value: int


@dataclass(
)
class EmptyMultiLine:
    value: int


@decorator
@dataclass
@another
class Decorated:
    value: int


@dataclass[int]
class Subscripted:
    value: int


class NotADataclass:
    value: int


@dataclass
class ExternalBase(ThirdParty):
    value: int


@dataclass
class ExternalAttribute(external_pkg.Base):
    value: int


@dataclass
class ExternalSubscript(ThirdParty[int]):
    value: int


@dataclass
class BuiltinBase(dict):
    value: int


@dataclass
class BuiltinError(Exception):
    value: int


@dataclass
class BuiltinGeneric(list[int]):
    value: int


@dataclass
class OddBuiltin(help):
    value: int


@dataclass
class WindowsBase(WindowsError):
    value: int


@dataclass
class ObjectBase(object):
    value: int


@dataclass
class ProjectBase(Options):
    value: int


@dataclass
class ProjectModule(project_base.Options):
    value: int


@dataclass
class AbcBase(ABC):
    value: int


@dataclass
class AbcAttribute(abc.ABC):
    value: int


@dataclass
class TypingBase(Protocol):
    value: int


@dataclass
class TypingGeneric(Generic[T]):
    value: int


@dataclass
class TypingNamedTuple(NamedTuple):
    value: int


@dataclass
class TypingExtensions(TypedDict):
    value: int


@dataclass
class ContainerBase(container.Sized):
    value: int


@dataclass
class RelativeBase(Sibling):
    value: int


@dataclass
class LocalBase(Bare):
    value: int


@dataclass
class AssignedBase(Renamed):
    value: int


@dataclass
class UnknownBase(Mystery):
    value: int


@dataclass
class CallBase(make()):
    value: int


@dataclass
class CachedProperty:
    value: int

    @cached_property
    def computed(self):
        return 1


@dataclass
class QualifiedCachedProperty:
    value: int

    @functools.cached_property
    def computed(self):
        return 1


@dataclass
class NestedCachedProperty:
    value: int

    class Inner:
        @cached_property
        def computed(self):
            return 1


def scope():
    @dataclass(slots=True)
    class Local:
        value: int = field(default=1)

    return Local
