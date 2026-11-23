from typing import Generic, TypeVar

T = TypeVar("T")


class Box(Generic[T]):
    value: T
