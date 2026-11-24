"""Functions that repeat each other."""
import functools


def alpha(value: int, /, other=1, *rest, key=None, **extra) -> int:
    """A docstring is not part of the shape."""
    total = value + other
    if total > 2:
        total -= 1
    elif total < 0:
        total = 0
    return total


async def beta(value: int) -> int:
    total = value
    await value
    return total


def too_short(value):
    return value


def docstring_is_not_counted(value):
    """Only two statements follow."""
    first = value
    return first


def elif_counts(value):
    if value:
        pass
    elif value is None:
        pass


def else_does_not_count(value):
    if value:
        pass
    else:
        pass


@functools.cache
def decorated(value):
    first = value
    second = first
    return second


def generic[T: int = int](value: T) -> T:
    first = value
    second = first
    return second


def nested_blocks(value):
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


class Holder:
    def method_is_ignored(self, value):
        first = value
        second = first
        return second
