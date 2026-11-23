from typing import Generic, TypeVar

Generic = object
T = TypeVar("T")


class Box(Generic[T]):
    value: T
