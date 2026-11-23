class ValueError(Exception):
    pass


def check(wanted: tuple[str, ...], available: tuple[str, ...]):
    for name in wanted:
        if name in {item for item in available}:
            raise ValueError("custom")
    return None
