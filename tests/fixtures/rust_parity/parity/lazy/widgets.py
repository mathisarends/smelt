"""The target of the lazy table."""


class Widget:
    def render(self):
        return self


class Hidden:
    pass


def helper():
    return Widget()
