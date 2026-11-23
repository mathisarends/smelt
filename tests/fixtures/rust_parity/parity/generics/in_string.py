from typing import Generic, TypeVar

T = TypeVar("T")
NOTE = "T stays referenced"


class Box(Generic[T]):
    value: T
