"""A nested project on Python 3.9 excludes both version-gated rules."""

from typing import Literal


class Event:
    kind: Literal["legacy"]


def chain(value):
    if isinstance(value, int):
        pass
    elif isinstance(value, str):
        pass
