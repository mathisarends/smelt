"""Name suggestions shared by public-member review rules."""


def private_name(name: str, occupied: frozenset[str]) -> str:
    preferred = f"_{name.lstrip('_')}"
    if preferred not in occupied:
        return preferred
    candidate = f"{preferred}_internal"
    suffix = 2
    while candidate in occupied:
        candidate = f"{preferred}_internal_{suffix}"
        suffix += 1
    return candidate
