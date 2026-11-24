import functools


def _first():
    return 1


def second():
    return 2


@functools.cache
def _third():
    return 3


def fourth(value: list[int] = []) -> None:
    return None


def _fifth(value: int | None = None) -> int | None:
    return value


def sixth():
    return 6


registry = {}
registry["key"] = sixth


def _seventh():
    return 7


def eighth():
    return 8
x = 1; y = 2


def _ninth():
    return 9


def tenth[T](value: T) -> T:
    return value
