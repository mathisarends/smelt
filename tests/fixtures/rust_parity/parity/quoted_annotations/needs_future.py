"""Docstring."""

from typing import Annotated, Literal, Optional


def plain(a: "int", b: "list[str]" = (), *c: "tuple[int]", d: "A | B" = 1, **e: " str ") -> "None":
    f: "dict[str, int]" = {}
    return f


class Holder:
    field: list["Holder"]
    other: Optional["Holder"] = None
    literal: Literal["a", "b"]
    typed: typing.Literal["c"]
    metadata: Annotated["Holder", "meta"]
    bare_annotated: Annotated["Holder"]
    deep: dict[str, list["Holder"]]
    union: "Holder" | None
    tupled: tuple["A", "B"]
    called: make("value", key="ignored")
    dotted: mod.Type["Holder"]
    lambda_: (lambda x="default": "body")
    listed: ["A", "B"]
    mapping: {"key": "value"}
    compared: "a" < "b"
    comprehended: ["x" for y in "z" if "w"]
    sliced: items["a":"b"]
    prefixed: b"bytes"
