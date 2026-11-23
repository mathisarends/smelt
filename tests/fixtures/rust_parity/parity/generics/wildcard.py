from typing import *

T = TypeVar("T")


class Box(Generic[T]):
    value: T
