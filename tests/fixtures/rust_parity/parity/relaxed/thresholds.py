"""An override raises the limits of CPX006, CPX007 and VIS007 here."""

items = [1, 2]
two_loops = [(a, b) for a in items for b in items]
four_loops = [(a, b) for a in items for b in items for _ in items for _ in items]


class Moderate:
    def __init__(self, value):
        self.a = 1 if value else 2
        self.b = 3 if value else 4
        self.c = 5 if value else 6


class Excessive:
    def __init__(self, value):
        self.a = 1 if value else 2
        self.b = 3 if value else 4
        self.c = 5 if value else 6
        self.d = 7 if value else 8
        self.e = 9 if value else 0


def _one():
    return 1


def _two():
    return 2
