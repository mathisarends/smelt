"""Repeated narrowing asserts on a receiver."""


class Repeats:
    def first(self):
        assert self._stack is not None
        assert self._stack is not None

    def second(self):
        if self.ready:
            assert self._stack is not None
        else:
            assert isinstance(self._stack, list)

    def third(self):
        assert isinstance(self._stack, list), "message"
        assert self.a.b is not None and isinstance(self.c, int)

    async def fourth(self):
        assert (self.a.b is not None and self.d is not None) and isinstance(self.c, int)
        assert self.d is not None


class WalkOrder:
    def one(self):
        for item in self.items:
            assert self.x is not None
        else:
            assert self.y is not None
        while self.more:
            assert self.y is not None
        else:
            assert self.x is not None

    def two(self):
        try:
            assert self.x is not None
        except ValueError:
            assert self.y is not None
        except KeyError:
            assert self.x is not None
        else:
            assert self.y is not None
        finally:
            assert self.x is not None

    def three(self):
        if self.a:
            assert self.y is not None
        elif self.b:
            assert self.x is not None
        elif self.c:
            assert self.y is not None
        else:
            assert self.x is not None
        with self.lock:
            assert self.y is not None
        match self.state:
            case 1:
                assert self.x is not None
            case _:
                assert self.y is not None


class Skipped:
    @staticmethod
    def static(value):
        assert value.x is not None

    @staticmethod
    def static_two(value):
        assert value.x is not None

    def no_receiver():
        assert self.x is not None

    def star(*args):
        assert self.x is not None

    def nested(self):
        def inner(self):
            assert self.x is not None

        class Scope:
            assert self.x is not None

        return inner, Scope, lambda: None

    def other_forms(self):
        assert self.x is None
        assert self.x is not None is not None
        assert self.x != None
        assert other.x is not None
        assert self is not None
        assert self.call().x is not None
        assert isinstance(other.x, int)
        assert isinstance()
        assert isinstance(*self.x)
        assert issubclass(self.x, int)
        assert self.x is not None or self.y is not None


class PositionalOnly:
    def one(this, /):
        assert this.x is not None

    def two(this, /, other):
        assert this.x is not None

    @classmethod
    def three(cls):
        assert cls.x is not None

    @classmethod
    def four(cls):
        assert cls.x is not None
