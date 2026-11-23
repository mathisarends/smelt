from typing import Generic, TypeVar

T = TypeVar("T")


class One(Generic[T]):
    value: T


class Two(Generic[T]):
    value: T
