from typing import TypeVar


def _private():
    return 1


def public():
    return 1


T = TypeVar("T", bound=make())
