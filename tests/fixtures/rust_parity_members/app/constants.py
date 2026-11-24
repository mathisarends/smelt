"""Module constants and type aliases."""
from typing import TYPE_CHECKING

MIN_LENGTH = 8
MAX_LENGTH: int = 16
REBOUND = 1
REBOUND = 2
USED_ELSEWHERE = 3
IN_SIGNATURE = 4
lower_case = 5
_PRIVATE = 6
A, B = 1, 2
QUOTED = int

type Payload = str | bytes
type Wrapped = list[Payload]
type Exposed = list[Inner]
type Inner = int
type _Hidden = int


def public(value: Exposed, limit=IN_SIGNATURE) -> "QUOTED":
    return len(value) >= MIN_LENGTH


def _valid(value: Payload) -> bool:
    return len(value) <= MAX_LENGTH


class Model:
    size: Wrapped
    default = REBOUND

    def __init__(self):
        self.extra: Payload = b""


if TYPE_CHECKING:
    def checked(value: Payload) -> None: ...
