"""Assignments that write several instance attributes."""

import builtins
from functools import wraps


class Inbox:
    def __init__(self, log, control):
        self.log, self.control = log, control

    def lists(self, a, b):
        [self.a, self.b] = [a, b]

    def mixed_sequences(self, a, b):
        self.a, self.b = [a, b]

    def constants(self):
        self.a, self.b = 1, "two"

    def chained(self, value):
        self.a = self.b = value

    def chained_constant(self):
        self.a = self.b = self.c = None

    def indented(self, flag, a, b):
        if flag:
            self.a, self.b = a, b
        else:
            for item in ():
                self.c, self.d = a, b

    def parenthesized(self, a, b):
        (self.a, self.b) = (a, b)

    def parenthesized_values(self, a, b):
        self.a, self.b = (a), (b)

    def trailing_comment(self, a, b):
        self.a, self.b = a, b  # kept outside the statement

    def inner_comment(self, a, b):
        self.a, self.b = (  # lost
            a,
            b,
        )

    def multi_line(self, a, b):
        self.a, self.b = (
            a,
            b,
        )

    def same_line(self, a, b):
        marker = 1; self.a, self.b = a, b

    def same_line_after(self, a, b):
        self.a, self.b = a, b; marker = 1

    def call_value(self, a):
        self.a, self.b = a, make()

    def chained_call(self):
        self.a = self.b = make()

    def different_length(self, a):
        self.a, self.b = a

    def unequal(self, a, b, c):
        self.a, self.b = a, b, c

    def starred(self, a):
        *self.a, self.b = a

    def starred_value(self, a, b):
        self.a, self.b = *a, b

    def nested_tuple(self, a, b, c):
        (self.a, self.b), self.c = (a, b), c

    def mixed_target(self, a, b):
        self.a, other = a, b

    def mixed_chain(self, a):
        self.a = other = self.b = a

    def one_attribute(self, a, b):
        self.a, other = a, b

    def single(self, a):
        self.a = a

    def locals_only(self, a, b):
        x, y = a, b
        return x, y

    def deep_attribute(self, a, b):
        self.x.a, self.x.b = a, b

    def subscript(self, a, b):
        self.a[0], self.b[0] = a, b

    def other_receiver(self, other, a, b):
        other.a, other.b = a, b

    def with_context(self, a, b):
        with a:
            self.a, self.b = a, b
        try:
            self.c, self.d = a, b
        except ValueError:
            self.e, self.f = a, b
        match a:
            case 1:
                self.g, self.h = a, b

    def nested_scopes(self, a, b):
        def inner(self):
            self.a, self.b = a, b

        class Inner:
            def method(this):
                this.a, this.b = a, b

        return inner, Inner, lambda: None

    def renamed(this, a, b):
        this.a, this.b = a, b
        self.c, self.d = a, b

    def positional_only(this, /, a, b):
        this.a, this.b = a, b

    def no_arguments():
        self.a, self.b = 1, 2

    def star_only(*args):
        args.a, args.b = 1, 2

    def keyword_only(*, self):
        self.a, self.b = 1, 2

    async def asynchronous(self, a, b):
        self.a, self.b = a, b

    @staticmethod
    def static(self, a, b):
        self.a, self.b = a, b

    @classmethod
    def class_level(cls, a, b):
        cls.a, cls.b = a, b

    @builtins.staticmethod
    def builtin_static(self, a, b):
        self.a, self.b = a, b

    @builtins.classmethod
    def builtin_class(cls, a, b):
        cls.a, cls.b = a, b

    @property
    def decorated(self):
        self.a, self.b = 1, 2

    @wraps(print)
    def wrapped(self):
        self.a, self.b = 1, 2

    @make().staticmethod
    def odd_decorator(self):
        self.a, self.b = 1, 2

    def __new__(cls, a, b):
        cls.a, cls.b = a, b

    def __init_subclass__(cls, a, b):
        cls.a, cls.b = a, b

    def __class_getitem__(cls, a, b):
        cls.a, cls.b = a, b

    class Nested:
        def method(self, a, b):
            self.a, self.b = a, b


def function(self, a, b):
    self.a, self.b = a, b


def scope():
    class Local:
        def method(self, a, b):
            self.a, self.b = a, b

    return Local
