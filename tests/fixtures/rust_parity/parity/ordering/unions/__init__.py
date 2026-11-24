def _private(value: int | None = None, other: str | bytes = "") -> int | None:
    return value


def public(value: list | dict | None = None) -> type | None:
    return None


def _shadowing(value: int | Missing = 1) -> None:
    return None


def visible():
    return 1
