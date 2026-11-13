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


def rename_advice(name: str, suggested: str) -> str:
    """Repair wording that names the target only when a collision changes it.

    Keeping the usual case free of names lets text output list it once per rule.
    """
    if suggested == f"_{name.lstrip('_')}":
        return "add a leading underscore"
    return f"rename to {suggested} (the underscored name is taken)"
