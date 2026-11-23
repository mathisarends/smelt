"""Aggregates that are not the builtin."""

from helpers import sum
from .local import any as any


def max(values):
    return values


def run(items):
    show(sum(item for item in items))
    show(any(item for item in items))
    show(max(item for item in items))
    show(min(item for item in items))
    show([item for item in items])
