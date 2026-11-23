"""MOD011: relative imports are not resolved, and rebinding an import hides it."""

from dataclasses import dataclass as rebound

from .frozen import dataclass


@dataclass(frozen=True)
class Relative:
    values: list[int]


rebound = dataclass


@rebound(frozen=True)
class Rebound:
    values: list[int]
