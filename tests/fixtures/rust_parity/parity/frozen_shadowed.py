"""MOD011: a builtin bound anywhere in the file is no longer recognized."""

from dataclasses import dataclass


def helper(list):
    return list


@dataclass(frozen=True)
class Shadowed:
    values: list[int]
    mapping: dict[str, int]
