from typing import Generic
from typing_extensions import ParamSpec, TypeVar  # trailing

T = TypeVar("T")
P = ParamSpec("P")


class Box(Generic[T, P]):
    value: T
