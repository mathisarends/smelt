from typing import Generic, Sequence, TypeVar

T = TypeVar("T")


class Box(Generic[T]):
    value: T


def first(items: Sequence[T]) -> T:
    return items[0]
