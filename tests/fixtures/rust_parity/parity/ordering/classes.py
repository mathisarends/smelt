from typing import overload


class Record:
    def _secret(self):
        return 1

    def __repr__(self):
        return "Record"

    def __init__(self):
        self.value = 1

    name = "record"

    @property
    def size(self):
        return 1

    @size.setter
    def size(self, value):
        pass

    @staticmethod
    def make():
        return Record()

    @classmethod
    def build(cls):
        return cls()

    @overload
    def load(self, value: int) -> int: ...

    @overload
    def load(self, value: str) -> str: ...

    def load(self, value):
        return value

    def public(self):
        return self._secret()


class Split:
    def _hidden(self):
        return 1

    @property
    def area(self):
        return 1

    def other(self):
        return 2

    @area.setter
    def area(self, value):
        pass

    def visible(self):
        return 3


class Outer:
    def _first(self):
        return 1

    class Inner:
        pass

    def second(self):
        return 2


class Based(dict):
    def _private(self):
        return 1

    def public(self):
        return 2


@decorated
class Marked:
    def _private(self):
        return 1

    def public(self):
        return 2


def decorated(cls):
    return cls
