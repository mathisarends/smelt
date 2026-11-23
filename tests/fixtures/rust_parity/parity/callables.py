"""IDM003: classes with one public method, and their signatures."""


def decorator(target):
    return target


class Service:
    """A docstring does not count."""

    def __init__(self, storage, *, retries: int = 3):
        self.storage = storage

    def generate(self, report_id, *, refresh=False):
        return report_id, refresh


class NoConstructor:
    pass

    def run(self):
        return 1


class Asynchronous:
    async def fetch(self, a, /, b=1, *args: int, c, d=2, **kwargs: str) -> int:
        return a


class PositionalOnly:
    def __init__(self, /, a, b=2):
        self.a = a

    def go(self, x=1, /, y=2):
        return x, y


class TwoPositionalOnly:
    def go(self, x, /):
        return x


class DefaultOnReceiver:
    def go(self=None, x=1, *, y):
        return x, y


class OnlyVariadic:
    def go(self, *args):
        return args


class OnlyKeywords:
    def go(self, **kwargs):
        return kwargs


class WithPrivate:
    def _helper(self):
        return 1

    def run(self):
        return self._helper()


class Annotated:
    def go(self, x: "list[int]" = [], y: int | None = None) -> None:
        return None


class Literal:
    "A string body."
    'Another one.'

    def run(self, value=b"bytes", other=-1.5, third=lambda q: q):
        return value


class TwoPublic:
    def first(self):
        return 1

    def second(self):
        return 2


class AlreadyCallable:
    def __call__(self):
        return 1

    def run(self):
        return 2


class WithBase(Service):
    def run(self):
        return 1


class EmptyParentheses():
    def run(self):
        return 1


class WithMetaclass(metaclass=type):
    def run(self):
        return 1


@decorator
class DecoratedClass:
    def run(self):
        return 1


class DecoratedMethod:
    @staticmethod
    def run():
        return 1


class NoReceiver:
    def run():
        return 1


class OtherSpecial:
    def __repr__(self):
        return ""

    def run(self):
        return 1


class Duplicate:
    def run(self):
        return 1

    def run(self):
        return 2


class WithData:
    limit = 3

    def run(self):
        return self.limit


class Conditional:
    if True:
        def run(self):
            return 1


class Generic[T]:
    def run(self, value: T) -> T:
        return value


def factory():
    class Nested:
        def run(self):
            return 1

    return Nested
