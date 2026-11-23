"""CPX008: methods called directly on new instances."""

import collections
import collections as coll
import os.path
from datetime import datetime as Moment
from decimal import Decimal, getcontext
from fractions import Fraction, _Private as _Hidden
from pathlib import PurePath as path_class

from . import frozen as sibling
from .frozen import Direct, Plain as Renamed


class Local:
    def run(self):
        return self

    @classmethod
    def build(cls):
        return Local().run()


class Rebound:
    def run(self):
        return self


Rebound = Local

assigned = Local().run()
annotated: Local = Local().run()
declared: Local
Local().run()
through_module = collections.OrderedDict().copy()
through_alias = coll.Counter(a=1).most_common(1)
dotted = os.path.PathLike().__fspath__()
aliased = Moment(2026, 1, 1).isoformat()
imported = Decimal("1").normalize()
lowercase = getcontext().copy()
private_name = _Hidden().value()
lowercase_alias = path_class("a").as_posix()
relative = Direct().run()
relative_alias = Renamed().run()
through_sibling = sibling.Direct().run()
rebound = Rebound().run()
builtin = dict().copy()
chained = Local().run().run()
nested = print(Local().run())
attribute = Local().attribute
argument = Local().run(Local().run())
parenthesized = (Local)().run()
spaced = Local(
    # comment
).run(
)
called_twice = Local()().run()
subscripted = Fraction[int]().limit()
tuple_value = Local().run(), Local().run()


def function(parameter):
    value = Local().run()
    if value:
        return Decimal("2").normalize()
    with parameter:
        Local().run()
    return None


async def coroutine():
    first = await Local().run()
    await Decimal("1").normalize()
    return await Local().run()


def shadow():
    Decimal = Local
    return Decimal().run()
