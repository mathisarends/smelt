from typing import Generic, TypeVar

T = TypeVar("T")  # dropped with the declaration


class Box(Generic[T]):
    value: T
