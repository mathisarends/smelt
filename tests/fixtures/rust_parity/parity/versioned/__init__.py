"""A nested project declaring Python 3.10, which excludes StrEnum suggestions."""

from typing import Literal


class Event:
    type: Literal["too-old-for-strenum"]


def chain(value):
    if isinstance(value, int):
        pass
    elif isinstance(value, str):
        pass
