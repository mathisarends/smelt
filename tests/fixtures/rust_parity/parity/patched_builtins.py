"""Writing to the builtins module makes its isinstance unreliable."""

import builtins

builtins.isinstance = print


def chain(value):
    if builtins.isinstance(value, int):
        pass
    elif builtins.isinstance(value, str):
        pass
