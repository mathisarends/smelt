"""CPX006: comprehension shapes, spans and digests."""

import math

items = [1, 2, 3]
other = [4, 5]
mapping = {1: 2}


def convert(value):
    return value


simple = [value for value in items]
single_filter = [value for value in items if value]
two_loops = [(a, b) for a in items for b in other]
three_loops = {(a, b, c) for a in items for b in other for c in items}
two_filters = [value for value in items if value if value > 1]
wide_filter = [value for value in items if value and value > 1 and value < 3]
narrow_filter = [value for value in items if value and value > 1]
nested_wide = [value for value in items if (value or (value and value and value))]
trivial_branch = [value if value else math.pi for value in items]
constant_branch = [1 if value else None for value in items]
call_branch = [convert(value) if value else None for value in items]
negative_branch = [-1 if value else 0 for value in items]
fstring_branch = [f"x" if value else "y" for value in items]
dict_branch = {key: convert(key) if key else key for key in mapping}
dict_loops = {key: value for key in mapping for value in other}
key_branch = {(convert(key) if key else key): key for key in mapping}
set_branch = {convert(value) if value else 0 for value in items}
generator = (convert(value) if value else 0 for value in items)
bare_generator = sum(a * b for a in items for b in other)
commented = sum(  # the parentheses belong to the generator
    a * b  # element
    for a in items  # first loop
    for b in other
)
parenthesized = sum((a * b for a in items for b in other))
parenthesized_inner = sum((a) * b for a in items for b in other)
two_arguments = max((a * b for a in items for b in other), default=0)
nested_inner = [[b for a in items for b in other] for _ in items]
nested_outer = [[a for a in items] for _ in items for _ in other]
in_iterable = [a for a in [b for b in items for _ in other]]
in_lambda = [(lambda: [a for a in items for _ in other]) for _ in items]
in_fstring = f"{[a for a in items for b in other]!r:>{sum(a for a in items for _ in other)}}"
in_subscript = mapping[next(a for a in items for _ in other)]
ünïcode = "äöü", [a for a in items for ä in other]
multiline = [
    a
    for a in items
    for b in other
    if a
    if b
]


@convert([a for a in items for b in other])
def decorated(
    first=[a for a in items for b in other],
    *,
    second: list[int] = [b for b in other if b if b > 1],
) -> [a for a in items for b in other]:
    return {a: b for a in items for b in other}


async def asynchronous(stream):
    collected = [value async for value in stream async for _ in stream]
    awaited = [await value for value in stream if value and value and value]
    return collected, awaited


class Holder:
    values = [a for a in items for b in other]

    def method(self):
        yield from (a for a in items for b in other)
        return (yield [a for a in items for b in other])
