"""Helpers with private names."""
import dataclasses
from dataclasses import dataclass
from typing import ClassVar, Optional

_cache = {}
_counter = 0
__version__ = "1"


def _normalize(value):
    return value


def public(value):
    return _normalize(value)


class Images:
    def _retain(self):
        return self

    def retain(self):
        return self._retain()


class SessionLog:
    _images: Images
    _limit: int = 3
    shared: ClassVar["Images"]
    maybe: Optional[Images]
    either: Images | None
    reversed: None | Images

    def __init__(self, images: Images, other):
        self._images = images
        self._other = other
        self._made = Images()
        self._count = 0
        self._count += 1
        self.__mangled = 1
        self._mixed = images

    def reset(self):
        self._mixed = Images()
        self._reset = 1

    @property
    def _view(self) -> Images:
        return self._images

    @staticmethod
    def _static():
        return 1

    def own(self):
        return self._images, self._limit, self._view

    def __repr__(self):
        return "log"


@dataclass
class Record:
    _secret: str
    log: SessionLog


@dataclasses.dataclass(frozen=True)
class Frozen:
    _value: int


def decorate(cls):
    return cls


@decorate
class Decorated:
    _hidden: int


class Child(SessionLog):
    pass


class Grandchild(Child):
    _own: Images


class Mixed(SessionLog, Images):
    pass


class Twice:
    _x: int


class Twice:
    _y: int
