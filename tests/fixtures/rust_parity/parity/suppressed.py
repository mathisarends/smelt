"""Inline suppressions in the positions the tokenizer distinguishes."""

from os import (  # smelt: ignore[CPX005] -- covers the whole import statement
    path,
    sep,  # smelt: ignore[CPX003] -- stays on this imported name
)

a = b = c = d = e = 1

hidden = a if b else c if d else e  # smelt: ignore[CPX005] -- exact code
by_name = a if b else c if d else e  # smelt: ignore[complexity/forbidden-nested-conditional] -- name
by_prefix = a if b else c if d else e  # smelt: ignore[cpx] -- lowercase prefix
every_code = a if b else c if d else e  # smelt: ignore -- every code
other_code = a if b else c if d else e  # smelt: ignore[CPX003] -- wrong code stays reported
multiline = (
    a if b else c if d else e  # smelt: ignore[CPX005] -- interior comment covers the statement
)
after_statement = (
    a if b else c if d else e
)  # smelt: ignore[CPX005] -- trailing comment covers the statement
text = "# smelt: ignore[CPX005]" if a else "b" if b else "c"


def hidden_function(a, b, c):  # smelt: ignore[CPX003] -- on the definition
    return a, b, c


def spanning_signature(  # smelt: ignore[CPX003] -- first line of the signature
    a,
    b,
    c,
):
    return a, b, c


def closing_comment(
    a,
    b,
    c,
):  # smelt: ignore[CPX003] -- closing line of the signature
    return a, b, c


@print  # smelt: ignore[CPX003] -- decorator lines are separate statements
def below_decorator(a, b, c):
    return a, b, c


# smelt: ignore[CPX003] -- a comment above does not reach the next line
def not_covered(a, b, c):
    return a, b, c
