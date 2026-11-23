"""Every way an ordinary module can bind __all__."""

import os as __all__, sys as __all__
from os import path as __all__
from somewhere import __all__

__all__ = ["a"]
__all__ += ["b"]
__all__: list[str] = []
first = __all__ = []
__all__, second = 1, 2
if first:
    __all__ = []
for __all__ in ():
    pass
with open("x") as __all__:
    pass
value = {1: (__all__ := 2), (__all__ := 3): 4}
value = (__all__ := 1) if (__all__ := 2) else 3
value = print(a=(__all__ := 1), *(__all__ := 2))
value = f"{(__all__ := 1)}"
type __all__ = int
__all__.attribute = 1
__all__[0] = 1
print(__all__)
del __all__
[__all__ for __all__ in ()]
value = lambda: (__all__ := 1)
try:
    pass
except Exception as __all__:
    pass
match value:
    case __all__:
        pass


def local():
    __all__ = []


class Local:
    __all__ = []
