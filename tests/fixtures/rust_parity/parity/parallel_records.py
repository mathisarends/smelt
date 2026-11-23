"""Stored sequences paired by position."""

import collections.abc
import typing
import typing as t
from collections.abc import MutableSequence, Sequence
from dataclasses import dataclass
from typing import ClassVar, List, Tuple


class Layout:
    models: tuple[type, ...]
    fields: tuple[tuple[str, ...], ...]
    names: list[str]
    quoted: "list[str]"
    typed: List[int]
    pairs: Tuple[int, ...]
    sequence: Sequence[int]
    mutable: MutableSequence[int]
    abc_sequence: collections.abc.Sequence[int]
    typing_sequence: typing.Sequence[int]
    aliased: t.List[int]
    bare_list: list
    plain: dict[str, int]
    optional: list[int] | None
    constant: ClassVar[list[int]]
    assigned: list[int] = []
    twice: list[int]
    twice: list[int]
    hidden: list[int]

    def hidden(self):
        return 1

    def split(self):
        for model, names in zip(self.models, self.fields, strict=True):
            yield model, names

    def same_group_again(self):
        return zip(self.fields, self.models)

    def three(self):
        return zip(self.models, self.fields, self.names)

    def spellings(self):
        a = zip(self.quoted, self.typed)
        b = zip(self.pairs, self.sequence)
        c = zip(self.mutable, self.abc_sequence)
        d = zip(self.typing_sequence, self.aliased)
        e = zip(self.bare_list, self.assigned)
        return a, b, c, d, e

    def skipped_fields(self):
        a = zip(self.names, self.plain)
        b = zip(self.names, self.optional)
        c = zip(self.names, self.constant)
        d = zip(self.names, self.twice)
        e = zip(self.names, self.hidden)
        f = zip(self.names, self.unknown)
        return a, b, c, d, e, f

    def one_field(self):
        return zip(self.names, self.names)

    def single(self):
        return zip(self.names)

    def empty(self):
        return zip()

    def starred(self):
        return zip(self.names, *self.fields)

    def with_local(self, other):
        return zip(self.names, other)

    def different_receiver(self, other):
        return zip(self.names, other.fields)

    def through_alias(self):
        names = self.names
        fields: tuple = self.fields
        return zip(names, fields)

    def alias_rebound(self):
        names = self.names
        names = self.fields
        fields = self.fields
        return zip(names, fields)

    def alias_after(self):
        result = zip(names, fields)
        names = self.names
        fields = self.fields
        return result

    def alias_same_line(self):
        names = self.names; result = zip(names, self.fields)
        return result

    def alias_conditional(self, flag):
        if flag:
            names = self.names
        return zip(names, self.fields)

    def alias_of_alias(self):
        names = self.names
        again = names
        return zip(again, self.fields)

    def alias_two_targets(self):
        names = other = self.names
        return zip(names, self.fields, other)

    def alias_bare_annotation(self):
        names: list[str]
        return zip(names, self.fields)

    def alias_tuple(self):
        names, fields = self.names, self.fields
        return zip(names, fields)

    def receiver_rebound(self):
        self = other
        return zip(self.names, self.fields)

    def receiver_deleted(self):
        pair = zip(self.names, self.fields)
        del self
        return pair

    def receiver_in_comprehension(self):
        return [zip(self.names, self.fields) for self in others]

    def nested_scopes(self):
        def inner(self):
            return zip(self.names, self.fields)

        class Scope:
            pair = zip(self.names, self.fields)

        return inner, Scope, lambda: zip(self.names, self.fields)

    def inside_comprehension(self):
        return [pair for pair in zip(self.names, self.fields)]

    def inside_statement(self, flag):
        while flag:
            zip(self.names, self.fields)
        with open(zip(self.names, self.fields)):
            pass
        return f"{zip(self.names, self.fields)}"

    def other_call(self):
        return map(self.names, self.fields)

    @property
    def decorated(self):
        return zip(self.names, self.fields)

    @staticmethod
    def static(self):
        return zip(self.names, self.fields)

    def no_receiver():
        return zip(self.names, self.fields)

    def star_receiver(*args):
        return zip(self.names, self.fields)

    def positional_only(this, /):
        return zip(this.names, this.fields)

    def keyword_argument(self):
        return zip(self.names, self.fields, strict=self.typed)


@dataclass
class Inherits(Layout):
    def split(self):
        return zip(self.names, self.fields)


def scope():
    class Local:
        left: list[int]
        right: list[int]

        def run(self):
            return zip(self.left, self.right)

    return Local
