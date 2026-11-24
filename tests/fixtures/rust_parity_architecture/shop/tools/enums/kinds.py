import enum
from enum import Enum, StrEnum, auto
from enum import StrEnum as Renamed
from enum import auto as make


class Color(StrEnum):
    RED = "red"
    GREEN = auto()
    MADE = make()
    DOTTED_AUTO = enum.auto()
    ALIAS = RED
    BAD_ALIAS = MISSING
    _private = "x"
    TYPED: str = "typed"
    BARE: str
    FIRST = SECOND = "both"
    NUMBER = 1
    CALLED = str()
    WITH_ARGUMENT = auto(1)
    obj.attribute = "x"
    TUPLE, PAIR = "a", "b"


class Dotted(enum.StrEnum):
    ONE = "one"


class Aliased(Renamed):
    ONE = "one"


class PlainEnum(Enum):
    ONE = "one"


class MixedIn(str, Enum):
    ONE = "one"


class Reserved(StrEnum):
    value = "x"
    ONE = "one"


class ReservedWalrus(StrEnum):
    ONE = (value := "one")


class ReservedLambda(StrEnum):
    ONE = lambda value: value


class ReservedMethod(StrEnum):
    ONE = "one"

    def __init__(self):
        pass


class ReservedImport(StrEnum):
    import value
    ONE = "one"


class ReservedGlobal(StrEnum):
    global value
    ONE = "one"


class Keyworded(StrEnum, metaclass=type):
    ONE = "one"


def decorator(cls):
    return cls


@decorator
class Decorated(StrEnum):
    ONE = "one"


class Rebound(StrEnum):
    ONE = "one"


Rebound = 1


class Child(Color):
    ONE = "one"


if True:
    class Conditional(StrEnum):
        ONE = "one"

used = Color.RED.value
wide = Color.GREEN . value
aliased = Color.ALIAS.value
private = Color._private.value
typed = Color.TYPED.value
bare = Color.BARE.value
first = Color.FIRST.value, Color.SECOND.value
number = Color.NUMBER.value
called = Color.CALLED.value
made = Color.MADE.value, Color.DOTTED_AUTO.value, Color.WITH_ARGUMENT.value
bad = Color.BAD_ALIAS.value, Color.TUPLE.value, Color.PAIR.value
others = (
    Dotted.ONE.value,
    Aliased.ONE.value,
    PlainEnum.ONE.value,
    MixedIn.ONE.value,
    Reserved.ONE.value,
    ReservedWalrus.ONE.value,
    ReservedLambda.ONE.value,
    ReservedMethod.ONE.value,
    ReservedImport.ONE.value,
    ReservedGlobal.ONE.value,
    Keyworded.ONE.value,
    Decorated.ONE.value,
    Rebound.ONE.value,
    Child.ONE.value,
    Conditional.ONE.value,
)
text = f"{Color.RED.value!r:>{Color.GREEN.value}}"
called_value = Color.RED.value.upper()
chained = Color.RED.name.value
