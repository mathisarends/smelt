import abc
from abc import abstractmethod as abstract


class Service:
    limit = 1

    def __init__(self) -> None:
        self._state = 0
        self.public = 1

    async def fetch(self) -> int:
        return self._state

    @staticmethod
    async def build() -> int:
        return 1

    @abstract
    async def declared(self) -> int:
        return 2

    @abc.abstractmethod
    async def qualified(self) -> int:
        return 3

    @property
    async def excluded(self) -> int:
        return 4

    def plain(self) -> int:
        return self._state

    def _hidden(self) -> int:
        return self._state


class Worker:
    async def run(self) -> None:
        return None

    def _step(self) -> None:
        return None


def _normalize(value: str) -> str:
    return value.lower()


def normalize(value: str) -> str:
    return _normalize(value)


_CONSTANT = 1
