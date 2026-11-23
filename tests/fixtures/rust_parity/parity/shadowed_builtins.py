"""A rebound isinstance disables IDM002 for the whole file."""


def isinstance(value, kind):
    return True


def chain(value):
    if isinstance(value, int):
        pass
    elif isinstance(value, str):
        pass
