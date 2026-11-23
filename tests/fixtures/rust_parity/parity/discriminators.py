"""Discriminator fields for MOD003."""

import typing
import typing_extensions as te
from typing import Annotated, ClassVar, Literal as Lit, NotRequired, Required


class Event:
    type: Lit["checkpoint.extension"]
    kind: typing.Literal["it's"] = "it's"
    tag: te.Literal['say "hi"']
    other: Lit["ignored"]


class Wrapped:
    type: Annotated[Lit["annotated"], "meta"]
    kind: Required[Lit["required"]]
    tag: NotRequired[Annotated[Lit["nested"], 1]]


class Quoted:
    type: "Lit['quoted']"
    kind: "Annotated[Lit['é\\n'], 1]"
    tag: " Lit['indented']"


class Excluded:
    type: Lit["a", "b"]
    kind: Lit[1]
    tag: ClassVar[Lit["class-var"]]


class Spelled:
    (type): Lit["parenthesized"] = "parenthesized"
    kind: (Lit["wrapped"])
    tag: Lit["implicit" " concatenation"] = (
        "implicit concatenation"
    )


def factory():
    class Local:
        type: Lit["\t\x7f­\U0001F600"]

    return Local


if True:

    class Conditional:
        @staticmethod
        def method():
            class Inner:
                kind: Lit["deep"]

            return Inner
