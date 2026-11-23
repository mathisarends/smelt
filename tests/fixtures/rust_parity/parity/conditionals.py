"""Conditional expressions in every position the walk has to reach."""

a = b = c = d = e = 1

simple = a if b else c
in_body = (a if b else c) if d else e
in_else = a if b else c if d else e
in_both = (a if b else c) if d else (a if b else e)
chain = a if b else c if d else e if a else b
in_test = a if (b if c else d) else e
in_call = print(a if b else c) if d else e
inner_chain_in_call = print(a if b else c if d else e)
in_list = [a if b else c if d else e, (a if b else c) if d else e]
in_lambda = lambda: a if b else c if d else e
in_comprehension = [a if b else c if d else e for _ in ()]
in_fstring = f"{a if b else c if d else e} and {(a if b else c) if d else e!r:>{a if b else c}}"
spanning = (
    a
    if b
    else (
        c
        if d
        else e
    )
)
ünicode = "ä" if a else "ö" if b else "ü"


def default(value=a if b else c if d else e) -> "a if b else c":
    return value if value else a if b else c


@print(a if b else c if d else e)
class Decorated(object if a else type if b else int):
    field: int = a if b else c if d else e
