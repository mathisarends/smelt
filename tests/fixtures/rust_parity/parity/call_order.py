"""Callers placed after the helpers they call."""

import os

LIMIT = 3


def helper():
    return 1


def caller():
    return helper()


async def async_helper():
    return 2


async def async_caller():
    return await async_helper()


def _private_helper():
    return 3


def public_of_private():
    return _private_helper()


def _private_caller():
    return _private_helper()


def _private_of_public():
    return helper()


def first_cycle():
    return second_cycle()


def second_cycle():
    return first_cycle()


def calls_cycle():
    return first_cycle(), second_cycle()


def self_recursive():
    return self_recursive()


def many():
    return helper(), caller(), async_helper()


def nested_calls():
    return print(helper(caller()))


def shadowed_local():
    helper = print
    return helper()


def shadowed_parameter(helper):
    return helper()


def shadowed_vararg(*helper):
    return helper()


def shadowed_kwarg(**helper):
    return helper()


def shadowed_keyword_only(*, helper):
    return helper()


def shadowed_loop():
    for helper in ():
        pass
    return helper()


def shadowed_walrus():
    print(helper := 1)
    return helper()


def shadowed_import():
    import helper

    return helper()


def shadowed_from():
    from os import path as helper

    return helper()


def shadowed_handler():
    try:
        pass
    except ValueError as helper:
        pass
    return helper()


def shadowed_match():
    match 1:
        case [*helper]:
            pass
    return helper()


def shadowed_mapping():
    match 1:
        case {"k": 1, **helper}:
            pass
    return helper()


def shadowed_as():
    match 1:
        case 1 as helper:
            pass
    return helper()


def shadowed_global():
    global helper
    return helper()


def shadowed_definition():
    def helper():
        pass

    return helper()


def shadowed_class():
    class helper:
        pass

    return helper()


def in_comprehension():
    return [helper() for item in ()]


def in_generator():
    return list(helper() for item in ())


def in_lambda():
    return lambda: helper()


def in_nested():
    def inner():
        return helper()

    return inner


def in_class():
    class Inner:
        value = helper()

    return Inner


def in_branches(flag):
    if flag:
        return helper()
    elif flag:
        return caller()
    try:
        return many()
    except helper():
        return nested_calls()
    finally:
        print(f"{async_helper()}")


def default_reads(value=helper()):
    return helper(), value


def annotation_reads(value: helper):
    return helper(), value


def return_reads() -> helper:
    return helper()


def keyword_default_reads(*, value=helper):
    return helper(), value


def vararg_annotation(*value: helper):
    return helper(), value


def kwarg_annotation(**value: helper):
    return helper(), value


REBOUND = 1


def eager_default(value=REBOUND):
    return helper(), value


def eager_annotation(value: LIMIT):
    return helper(), value


def _before_rebound():
    return 1


CHANGED = 2


def _after_rebound(value=CHANGED):
    return _before_rebound(), value


def _plain_after():
    return _before_rebound()


# A comment that belongs to the caller.
# It has a second line.
def commented():
    return helper()


def trailing_comment():  # kept with the definition
    return helper()


def one_line(): return helper()


@staticmethod
def decorated_caller():
    return helper()


@staticmethod
def decorated_helper():
    return 1


def calls_decorated():
    return decorated_helper()


def duplicated():
    return 1


def duplicated():
    return 2


def calls_duplicated():
    return duplicated()


def __dunder_helper():
    return 1


def calls_dunder():
    return __dunder_helper()


if LIMIT:
    def guarded():
        return helper()


class Service:
    def run(self):
        return self.step()

    def step(self):
        return 1

    def after(self):
        return self.step()

    def _hidden(self):
        return 1

    def public_of_hidden(self):
        return self._hidden()

    def _private_after(self):
        return self._hidden()

    def bare_name(self):
        return step()

    def other_receiver(self, other):
        return other.step()

    def rebound(self):
        self = other
        return self.step()

    def nested(self):
        return [self.step() for item in ()]

    # A method comment.
    def commented(self):
        return self.step()

    def __dunder(self):
        return 1

    def calls_dunder(self):
        return self.__dunder()

    @property
    def decorated(self):
        return 1

    def calls_decorated(self):
        return self.decorated()

    def first_cycle(self):
        return self.second_cycle()

    def second_cycle(self):
        return self.first_cycle()


class PositionalOnly:
    def helper(self, /):
        return 1

    def caller(self, /):
        return self.helper()


class WithMetaclass(metaclass=type):
    def helper(self):
        return 1

    def caller(self):
        return self.helper()


class WithBase(Service):
    def helper(self):
        return 1

    def caller(self):
        return self.helper()


@staticmethod
class Decorated:
    def helper(self):
        return 1

    def caller(self):
        return self.helper()


class Twice:
    def helper(self):
        return 1


class Twice:
    def helper(self):
        return 1

    def caller(self):
        return self.helper()


class module:
    def helper(self):
        return 1

    def caller(self):
        return self.helper()
