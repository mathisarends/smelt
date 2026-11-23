"""Type dispatch chains for IDM002."""

import builtins as b
from builtins import isinstance as check


def simple(event):
    if isinstance(event, Started):
        start(event)
    elif isinstance(event, Delta | Progress):
        update(event)
    elif isinstance(event, (Finished, pkg.Done)) and event.ok:
        finish(event)
    else:
        other(event)


def guards(event):
    if check(event, A) and event.ok and not event.bad:
        pass
    elif b.isinstance(event, A) and (event.x or event.y) and event.z:
        pass
    elif isinstance(event, A):
        pass
    elif isinstance(event, A):
        pass


class Holder:
    def method(self, value):
        if isinstance(value, int):
            pass
        elif isinstance(value, str) and f"{value!r:>{width}}" != 'q"\n':
            pass


def not_chains(value, other):
    if isinstance(value, int):
        pass
    elif isinstance(other, str):
        pass

    if isinstance(value, int):
        pass
    else:
        if isinstance(value, str):
            pass

    if isinstance(value, int):
        pass
    elif value:
        pass

    if isinstance(value.attr, int):
        pass
    elif isinstance(value.attr, str):
        pass

    if isinstance(value, int) or value:
        pass
    elif isinstance(value, str):
        pass

    if isinstance(value, get()):
        pass
    elif isinstance(value, str):
        pass

    if isinstance(value, ()):
        pass
    elif isinstance(value, str):
        pass

    if isinstance(value, int) and (value := 1):
        pass
    elif isinstance(value, str):
        pass

    if isinstance(value, int, key=1):
        pass
    elif isinstance(value, str):
        pass


def nested(value):
    for item in value:
        try:
            if isinstance(item, int):
                pass
            elif isinstance(item, str):
                if isinstance(value, int):
                    pass
                elif isinstance(value, list):
                    pass
        finally:
            pass
