from dataclasses import dataclass


@dataclass
class OnlyNeedsFrozen:
    value: int


@dataclass(frozen=True)
class Satisfied:
    value: int
