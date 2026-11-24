_DEFAULT = 1


def _make(value=_DEFAULT):
    return value


def use(value=_DEFAULT, *, other=lambda: _missing, call=lambda item=_DEFAULT: item):
    return _make(value)


_DEFAULT = 2
_LOOKUP = {"a": _DEFAULT}
TABLE = [_LOOKUP, (1, 2), {"key": _DEFAULT}, {1, 2}]


def _late():
    return TABLE


def visible():
    return [item for item in TABLE]
