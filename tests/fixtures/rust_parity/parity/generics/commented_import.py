from typing import (
    Generic,  # keep
    Sized,
    TypeVar,
)

T = TypeVar("T")


class Box(Generic[T], Sized):
    value: T
