"""Module functions with different evidence."""
from builtins import staticmethod as static_alias


def unused():
    return 1


def internal_only():
    return unused


def caller():
    return internal_only()


def imported_elsewhere():
    return 2


def loaded_through_module():
    return 3


def tested_only():
    return 4


def tested_and_internal():
    return 5


async def async_internal():
    return tested_and_internal()


def run():
    return caller(), async_internal


@property
def decorated():
    return 6


def _private():
    return 7


def twice():
    return 1


def twice():
    return 2


class Browser:
    limit = 3
    names = ["a"]
    computed = dict(a=1)
    _hidden = 1

    def __init__(self, page):
        self.page = page
        self.state = "new"
        self.shared = None
        self._secret = 1
        self.tested = 0

    def emit(self, event):
        return event

    def run(self):
        self.emit("ready")
        self.state = "running"
        return self.page, self.limit, self.names, self.tested

    def external(self):
        return 1

    def unused_method(self):
        return 2

    async def fetch(self):
        return self.emit("fetch")

    @staticmethod
    def normalize(value):
        return value.strip()

    @staticmethod
    def __dunder_static(value):
        return value

    @static_alias
    def aliased(value):
        return value

    @staticmethod
    def needs_class(value):
        return Browser(value)

    @staticmethod
    def needs_limit(value):
        return value * limit_alias

    limit_alias = 2

    @staticmethod
    def used_outside(value):
        return value

    @classmethod
    def build(cls):
        return cls(None)

    @property
    def view(self):
        return self.page

    def __repr__(self):
        return "browser"

    def _internal(self):
        return self.normalize(" x ") + self.aliased(" y ")

    def uses_reflection(self):
        return getattr(self, "reflected"), self.used_outside(1)

    def reflected(self):
        return 1

    def collide(self):
        return self._collide

    def _collide(self):
        return 1

    def nested(self):
        def inner(self):
            return self.state

        handler = lambda self: self.page
        return [self.page for self in []], inner, handler


class Base:
    def hook(self):
        return 1


class Derived(Base):
    def hook(self):
        return 2


class Slotted:
    __slots__ = ("value",)

    def __init__(self):
        self.value = 1


class Exported:
    def exported_method(self):
        return 1


class Plain(object):
    def __init__(self):
        self.internal_value = 1
        self.module_value = 2

    def read(self):
        return self.internal_value


class Keywords(metaclass=type):
    def keyword_method(self):
        return 1


def reads_plain(plain: Plain):
    return plain.module_value


Browser("p").external()
