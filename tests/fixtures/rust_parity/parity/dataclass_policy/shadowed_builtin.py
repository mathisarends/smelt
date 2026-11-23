from dataclasses import dataclass


class float:
    pass


frozenset = 1


@dataclass
class LocalClass(float):
    value: int


@dataclass
class LocalAssignment(frozenset):
    value: int


@dataclass
class StillBuiltin(int):
    value: int
