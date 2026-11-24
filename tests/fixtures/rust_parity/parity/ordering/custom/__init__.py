_hidden = 1
LIMIT = 2
__all__ = ["visible"]


def _private():
    return 1


def visible():
    return 2


class Shape:
    def _private(self):
        return 1

    def __init__(self):
        self.size = 1

    name = "shape"
