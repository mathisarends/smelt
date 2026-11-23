"""Private literal constants declared on classes."""


class Controller:
    _EXTENSIONS = {".pdf", ".zip"}
    _LIMIT: int = 3
    _NAMES = ["a", "b"]
    _PAIRS = (1, 2.0, 3j)
    _MAPPING = {"key": [1, (2,)], 3: None}
    _NEGATIVE = -1
    _POSITIVE = +2.5
    _BYTES = b"raw"
    _FLAG = True
    _NOTHING = None
    _DOTS = ...
    _CHAINED = _OTHER = 1
    _NEGATIVE_BOOL = -True
    _NEGATIVE_TEXT = -"x"
    _NESTED_NEGATIVE = [-1, -2.0]
    _BAD_NESTED = [-True]
    _SPREAD = {**{}}
    _CALL = dict()
    _NAME_VALUE = _LIMIT
    _COMPREHENSION = [item for item in ()]
    _FSTRING = f"text"
    PUBLIC = 1
    _lower = 1
    _Mixed = 1
    __DUNDER__ = 1
    __MANGLED = 1
    _ = 1
    _1 = 1
    _ÄÖ = 1

    if True:
        _GUARDED = 1
    else:
        _ELSE = 2

    try:
        _TRY = 1
    except ValueError:
        _EXCEPT = 2
    finally:
        _FINAL = 3

    for _item in ():
        _LOOP = 1

    with open("x"):
        _WITH = 1

    match 1:
        case 1:
            _CASE = 1

    def method(self):
        _LOCAL = 1
        self._INSTANCE = 2
        return _LOCAL

    class Nested:
        _NESTED = 1

        def read(self):
            return self._NESTED


class Inherits(Controller):
    _SKIPPED = 1


class Keyworded(metaclass=type):
    _SKIPPED = 1


@staticmethod
class Decorated:
    _SKIPPED = 1


class EmptyParens():
    _COUNTED = 1
    other = 2


def scope():
    class Local:
        _LOCAL_CLASS = 1
        other = 2

    return Local


class OnlyConstant:
    _ALONE = 1


class Reads:
    _MOVABLE = {"a"}
    _ANNOTATED: frozenset[str] = frozenset()
    _ANNOTATED_LITERAL: int = 4
    (_PARENTHESIZED): int = 5
    _COMMENTED = 1  # stays with the constant
    _SAME_LINE = 1; _SECOND = 2
    _BLANK_AFTER = 3

    _MULTI_LINE = [
        1,
        2,
    ]
    _TEXT = """first
        indented
    back

        tail"""
    _UNREAD = 9
    _WRITTEN = 1
    _DELETED = 2
    _OTHER_RECEIVER = 3
    _NESTED_ATTRIBUTE = 4
    _ALSO_NAME = 5
    _IN_STRING = 6
    _GLOBAL_DECLARED = 7
    _BY_INSTANCE = 8

    def first(self):
        return self._MOVABLE, Reads._MOVABLE

    def nested_scope(self):
        def inner():
            return self._MOVABLE

        return [self._MOVABLE for item in ()], inner, lambda: self._MOVABLE

    def literal(self):
        return {self._ANNOTATED_LITERAL: Reads._ANNOTATED_LITERAL}, f"{self._COMMENTED}"

    def order(self, value=None):
        if value:
            print(self._BLANK_AFTER)
        elif Reads._BLANK_AFTER:
            print(Reads._BLANK_AFTER, key=self._BLANK_AFTER)
        return self._MULTI_LINE, self._TEXT

    def writes(self):
        self._WRITTEN = 2
        del self._DELETED

    def other_receiver(self, other):
        return other._OTHER_RECEIVER

    def nested_attribute(self):
        return self.inner._NESTED_ATTRIBUTE

    def name_read(self):
        return _ALSO_NAME

    def text_read(self):
        return "_IN_STRING"

    def global_read(self):
        global _GLOBAL_DECLARED

    @staticmethod
    def no_receiver():
        return Reads._BY_INSTANCE

    def __init__(this):
        this._BY_INSTANCE


outside = Reads._UNREAD


# A comment above the class.
class Commented:
    _MOVED = 1
    other = 2


class NoNewlineAfter:
    other = 1
    _LAST = 2
