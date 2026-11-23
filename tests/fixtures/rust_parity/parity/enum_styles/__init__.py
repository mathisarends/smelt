"""StrEnum values under the explicit default."""

import enum
import enum as stdlib_enum
from enum import StrEnum, auto
from enum import StrEnum as Base
from enum import auto as derive


class Plain(StrEnum):
    A = auto()
    B = "b"
    Mixed_Case = auto()


class Qualified(enum.StrEnum):
    A = enum.auto()


class Aliased(stdlib_enum.StrEnum):
    A = stdlib_enum.auto()


class Renamed(Base):
    A = derive()


class Annotated(StrEnum):
    A: str = auto()
    B: str


class Private(StrEnum):
    _A = auto()
    __B = auto()
    C = "c"


class OnlyLiterals(StrEnum):
    A = "a"
    B = "custom"


class Generated(StrEnum):
    A = auto()

    def _generate_next_value_(name, start, count, last_values):
        return name


class GeneratedAssign(StrEnum):
    A = auto()
    _generate_next_value_ = print


class GeneratedAsync(StrEnum):
    A = auto()

    async def _generate_next_value_(name, start, count, last_values):
        return name


class Duplicate(StrEnum):
    A = auto()
    A = auto()


class DuplicateMixed(StrEnum):
    A = auto()
    A = "a"


class WithArguments(StrEnum):
    A = auto(1)
    B = auto(key=1)
    C = other()


class Chained(StrEnum):
    A = B = auto()


class TwoBases(StrEnum, int):
    A = auto()


class NotEnum(Other):
    A = auto()


class Unicode(StrEnum):
    STRASSE = auto()
    ÄPFEL = auto()
    ΣΑΣ = auto()


def scope():
    class Local(StrEnum):
        A = auto()

    return Local
