"""Second names for classes and functions."""

import collections
import collections.abc as abc_module
import typing
import typing as t
from typing import Any, TypeAlias

from external import Imported, Mixed_Case, UPPER, lowercase
from typing_extensions import Special

from . import Widget
from .typing import FromLocalTyping


class Model: ...


def helper(): ...


class Twice: ...


class Twice: ...


def redefined(): ...


class redefined: ...


First = Model
Second: TypeAlias = Model
Third: typing.TypeAlias = helper
NotRecognized: t.TypeAlias = Model
type Fifth = Model
type Parametrized[T] = Model
Model = Model
Chained = AlsoChained = Model
SecondImport = Imported
low = lowercase
up = UPPER
Mix = Mixed_Case
special = Special
anything = Any
Ordered = collections.OrderedDict
Abstract = abc_module.Mapping
lower_attribute = collections.deque
typed = typing.List
unknown_owner = unknown.Thing
(Parenthesized) = Model
(AnnotatedParens): TypeAlias = Model
Rebound = Twice
ReboundKinds = redefined
Packaged = Widget
LocalTyping = FromLocalTyping
Number = 1
Union = Model | None
Bare: TypeAlias

if Number:
    Conditional = Model


class Holder:
    Inner = Model
