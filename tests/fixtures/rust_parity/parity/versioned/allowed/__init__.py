"""An override sets type_dispatch to allow, so IDM002 stays silent here."""


def chain(value):
    if isinstance(value, int):
        pass
    elif isinstance(value, str):
        pass
