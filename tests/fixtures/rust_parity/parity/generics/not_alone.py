from typing import Generic, TypeVar

T = TypeVar("T"); marker = 1


class Box(Generic[T]):
    value: T
