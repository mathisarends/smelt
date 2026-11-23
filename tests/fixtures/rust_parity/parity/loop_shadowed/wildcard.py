from somewhere import *


def check(wanted: tuple[str, ...], available: tuple[str, ...]):
    for name in wanted:
        if name in {item for item in available}:
            return name
    return None
