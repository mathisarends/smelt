import builtins


def _private(value: int | None = None) -> int | None:
    return value


def public():
    return 1


builtins.flag = True
