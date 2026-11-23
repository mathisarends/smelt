import typing as t
from typing_extensions import Generic as G, TypeVar as TV

A = TV("A")
B = t.TypeVar("B")


class One(G[A]):
    value: A


class Two(t.Generic[B]):
    value: B
