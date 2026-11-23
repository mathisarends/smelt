from typing import Generic, List, TypeVar, cast

T = TypeVar("T")


class Box(Generic[T]):
    value: List[T]


RESULT = cast(int, 1)
