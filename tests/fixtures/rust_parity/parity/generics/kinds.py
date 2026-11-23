from typing import Generic, ParamSpec, TypeVar, TypeVarTuple

T = TypeVar("T")
B = TypeVar("B", bound="Base")
C = TypeVar("C", int, str)
Q = TypeVar("Q", bound=list[int])
P = ParamSpec("P")
Ts = TypeVarTuple("Ts")


class Everything(Generic[T, B, C, Q, P, *Ts]):
    value: T
