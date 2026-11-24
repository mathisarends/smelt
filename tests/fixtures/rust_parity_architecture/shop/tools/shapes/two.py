"""The same functions under other names, and near misses."""
import functools


def alpha_copy(value: int, /, other=1, *rest, key=None, **extra) -> int:
    total = value + other
    if total > 2:
        total -= 1
    elif total < 0:
        total = 0
    return total


def alpha_default_differs(value: int, /, other=2, *rest, key=None, **extra) -> int:
    total = value + other
    if total > 2:
        total -= 1
    elif total < 0:
        total = 0
    return total


def beta_not_async(value: int) -> int:
    total = value
    await_free = value
    return total


async def beta_copy(value: int) -> int:
    """Async twin."""
    total = value
    await value
    return total


def elif_counts_copy(value):
    if value:
        pass
    elif value is None:
        pass


def else_does_not_count_copy(value):
    if value:
        pass
    else:
        pass


@functools.cache
def decorated_copy(value):
    first = value
    second = first
    return second


def undecorated(value):
    first = value
    second = first
    return second


def generic_copy[T: int = int](value: T) -> T:
    first = value
    second = first
    return second


def nested_blocks_copy(value):
    for item in value:
        try:
            print(item, f"{item!r:>{value}}", b"x", 1.5, 2j, ...)
        except ValueError as error:
            raise TypeError from error
        finally:
            del item
    match value:
        case {"a": 1, **rest}:
            print(rest)
        case [1, *others] | (2, others):
            print(others)
        case str(name=x) if x:
            print(x)
    with open(value) as handle, open(value):
        print(handle)
    return lambda a, *b, c=1, **d: (a, b, c, d)
