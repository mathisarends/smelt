"""Suppressions that hide findings, and suppressions that hide nothing."""


def used_code(first, second, third):  # smelt: ignore[CPX003] -- legacy signature
    return first, second, third


def used_name(first, second, third):  # smelt: ignore[complexity/excess-positional-parameters] -- legacy
    return first, second, third


def used_prefix(first, second, third):  # smelt: ignore[CPX] -- whole family
    return first, second, third


def lower_case(first, second, third):  # smelt: ignore[cpx003] -- spelled in lower case
    return first, second, third


def partly_unused(first, second, third):  # smelt: ignore[CPX003, CPX005] -- one is stale
    return first, second, third


def without_reason(first, second, third):  # smelt: ignore[CPX003]
    return first, second, third


def bare(first, second, third):  # smelt: ignore -- everything on this line
    return first, second, third


def spanning(  # smelt: ignore[CPX003] -- the header spans lines
    first,
    second,
    third,
):
    return first, second, third


def nested(flag, other):
    return 1 if flag else 2 if other else 3  # smelt: ignore[CPX005, CPX003] -- nested on purpose


UNUSED = 1  # smelt: ignore[CPX003] -- nothing here
BARE_UNUSED = 2  # smelt: ignore -- nothing here either
EMPTY_REASON = 3  # smelt: ignore[CPX003] --
INACTIVE = 4  # smelt: ignore[TYP001] -- a known rule this run does not enable
TEXT = "# smelt: ignore[CPX003] -- inside a string"
# smelt: ignore[CPX003] -- a comment on its own line
NÄME = "ü"  # smelt: ignore[CPX003] -- non-ASCII before the comment
MIXED = 5  # smelt:ignore[ CPX003 ,  CPX005 ]--tight spacing
