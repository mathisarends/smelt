"""StrEnum values under the auto convention."""

import enum
from enum import StrEnum, auto


class Redundant(StrEnum):
    A = "a"
    B = "b"
    C = auto()


class Custom(StrEnum):
    A = "custom"
    B = auto()
    C = "c"


class OnlyCustom(StrEnum):
    A = "mouseMoved"


class Qualified(enum.StrEnum):
    A = "a"


class Generated(StrEnum):
    A = "a"

    def _generate_next_value_(name, start, count, last_values):
        return name


class Empty(StrEnum):
    pass


class Duplicate(StrEnum):
    A = "a"
    A = "a"
    B = "b"


class Annotated(StrEnum):
    A: str = "a"
