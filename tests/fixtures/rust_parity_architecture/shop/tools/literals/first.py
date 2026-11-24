from __future__ import annotations

import typing
import typing as tp
from typing import Annotated, Literal, Optional, TypeAlias

import typing_extensions as te
from typing_extensions import Literal as Lit

type Plain = Literal["left", "right"]
type Generic[T] = Literal["left", "right"] | T
Alias = Literal["right", "left", "left"]
Explicit: TypeAlias = Literal["left", "right"]
Wrapped = Annotated[Literal["left", "right"], Literal["meta", "data"]]
NotAlias = Optional[Literal["left", "right"]]
Quoted: "Literal['left', 'right']" = "left"
DoubleQuoted: "'Literal[\"left\", \"right\"]'" = "left"
Broken: "Literal[" = "left"
Nested: Literal[Literal["left"], "right"] = "left"
Dotted: typing.Literal["left", "right"] = "left"
Renamed: tp.Literal["left", "right"] = "left"
Extension: te.Literal["left", "right"] = "left"
FromExtension: Lit["left", "right"] = "left"
Union: Literal["left", "right"] | Literal["up", "down"] | None = None
Container: dict[str, list[Literal["up", "down"]]] = {}
Callback: typing.Callable[[Literal["up", "down"]], tuple[Literal["a", "b"], ...]]
Single: Literal["only"] = "only"
Duplicated: Literal["only", "only"] = "only"
Numbers: Literal[1, -1, +2, 0, -0, 0x10, True, False, None]
NumbersAgain: Literal[None, False, True, 16, -0, 0, 2, -1, 1]
Big: Literal[123456789012345678901234567890, -123456789012345678901234567890]
BigAgain: Literal[-123456789012345678901234567890, 123456789012345678901234567890]
Text: Literal["ä", "😀", "it's", 'say "hi"', "both ' and \"", "tab\t", "\x7f", "\\"]
TextAgain: Literal["😀", "ä", "it's", 'say "hi"', "both ' and \"", "tab\t", "\x7f", "\\"]
Mixed: Literal[0, False]
MixedAgain: Literal[False, 0]
ZeroOne: Literal[1, True]
ZeroOneAgain: Literal[True, 1]
Bytes: Literal[b"a", "b"]
Float: Literal[1.5, 2]
Negative: Literal[-1.5, 2]
NameMember: Literal[Plain, "b"]
Tuple: Literal[("x", "y")]
TupleAgain: Literal[("y", "x")]
Empty: Literal[()]
Attribute: obj.value = 1
Annotated_: Annotated[int, Literal["up", "down"]]
Multi: Literal[
    "up",
    "down",
]
one, two = Literal["a", "b"], Literal["a", "b"]
first = second = Literal["a", "b"]
notyping = dict["a", "b"]
target: "Literal['a', 'b'] | None" = None


def function(
    positional: Literal["a", "b"],
    /,
    regular: Literal["a", "b"] = "a",
    *variadic: Literal["a", "b"],
    keyword: Literal["a", "b"] = "a",
    **extra: Literal["a", "b"],
) -> Literal["a", "b"]:
    local: Literal["a", "b"] = "a"

    def inner(argument: "Literal['a', 'b']") -> None:
        nested: Literal["a", "b"] = "a"

    for _ in ():
        loop: Literal["a", "b"] = "a"
    else:
        loop_else: Literal["a", "b"] = "a"
    while local:
        spin: Literal["a", "b"] = "a"
    else:
        spin_else: Literal["a", "b"] = "a"
    if local:
        branch: Literal["a", "b"] = "a"
    elif regular:
        branch: Literal["a", "b"] = "b"
    else:
        branch: Literal["a", "b"] = "a"
    with open(local) as handle:
        held: Literal["a", "b"] = "a"
    try:
        tried: Literal["a", "b"] = "a"
    except ValueError:
        caught: Literal["a", "b"] = "a"
    else:
        orelse: Literal["a", "b"] = "a"
    finally:
        final: Literal["a", "b"] = "a"
    match local:
        case "a":
            matched: Literal["a", "b"] = "a"
    return local


async def coroutine(value: Literal["a", "b"]) -> Literal["a", "b"]:
    return value


def default_is_skipped(value=Literal["x", "y"]):
    decorated_value = Literal["x", "y"]


class Model:
    field: Literal["a", "b"]
    self_attr: "Literal['a', 'b']" = "a"

    class Inner:
        deep: Literal["a", "b"]

    def method(self, value: Literal["a", "b"]) -> None:
        self.attribute: Literal["a", "b"] = value
