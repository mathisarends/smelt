"""Only stubs."""
from typing import Protocol


class Port(Protocol):
    def send(self) -> None: ...

    def close(self) -> None:
        """Close the port."""

    def fail(self) -> None:
        raise NotImplementedError

    def nothing(self) -> None:
        pass

    def number(self) -> int:
        1
        None
        b"bytes"


async def wait() -> None: ...


LIMIT = 3
handler = lambda: 1  # noqa: E731
