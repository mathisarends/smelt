from typing import Generic, TypeVar

T = TypeVar("T")
U = TypeVar("U")


class Box(Generic[T]):
    value: T


class Refused(Generic[U, int]):
    value: U
