"""Raises the policy reports and the ones it leaves alone."""

import builtins
import builtins as native
from builtins import KeyError as Missing, ValueError as Wrong

import pydantic
from pydantic import field_validator, model_validator
from pydantic.functional_validators import field_validator as functional_validator

from .base import AppError


class LocalError(Exception): ...


class Shadow:
    pass


LookupError = AppError
IndexError: type = AppError
(ZeroDivisionError) = AppError
ArithmeticError, OverflowError = AppError, AppError


def plain(value):
    raise ValueError
    raise ValueError("message")
    raise RuntimeError("message") from value
    raise TypeError("allowed by the policy")
    raise NotImplementedError
    raise StopIteration
    raise StopAsyncIteration
    raise GeneratorExit
    raise KeyboardInterrupt
    raise SystemExit
    raise AppError("project")
    raise LocalError
    raise value
    raise
    raise value()
    raise make_error()()


def shadowing():
    raise LookupError
    raise IndexError
    raise ZeroDivisionError
    raise ArithmeticError
    raise OverflowError
    raise Wrong
    raise Missing("x")


def locally_bound():
    OSError = AppError
    raise OSError


def qualified():
    raise builtins.ValueError
    raise builtins.ValueError("x")
    raise native.KeyError
    raise builtins.NotImplementedError
    raise builtins.TypeError
    raise builtins.len
    raise builtins.nested.ValueError
    raise other.ValueError


def versions():
    raise PythonFinalizationError
    raise EnvironmentError
    raise IOError
    raise ExceptionGroup("a", [])
    raise BaseExceptionGroup("a", [])
    raise Warning
    raise UserWarning


def nested():
    def inner():
        raise ValueError

    class Scoped:
        def method(self):
            raise KeyError

        def __getitem__(self, key):
            raise KeyError(key)

    return inner, Scoped


class Protocols:
    def __init__(self):
        raise ValueError

    def __post_init__(self):
        raise ValueError

    def __new__(cls):
        raise ValueError

    def __call__(self):
        raise ValueError

    def __getattr__(self, name):
        raise AttributeError(name)

    def __iter__(self):
        def inner():
            raise ValueError

        return inner

    def __(self):
        raise ValueError

    @field_validator("a")
    def validated(cls, value):
        raise ValueError

    @model_validator(mode="after")
    def model_checked(self):
        raise ValueError

    @pydantic.validator("a")
    def old_validated(cls, value):
        raise ValueError

    @pydantic.root_validator
    def old_root(cls, values):
        raise ValueError

    @functional_validator("a")
    def functional(cls, value):
        raise ValueError

    @other_validator("a")
    def not_validated(cls, value):
        raise ValueError


if __name__:
    raise ValueError("module level")
