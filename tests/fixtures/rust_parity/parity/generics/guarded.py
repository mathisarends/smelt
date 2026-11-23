from typing import Generic, TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    T = TypeVar("T")


class Box(Generic[T]):
    value: T
