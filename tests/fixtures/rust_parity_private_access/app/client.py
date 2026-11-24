"""Callers outside the owning modules."""
from __future__ import annotations

import app.service
import app.service as svc
from typing import TYPE_CHECKING, Annotated

from app import service
from app.service import (
    Child,
    Decorated,
    Frozen,
    Grandchild,
    Images,
    Mixed,
    Record,
    SessionLog,
    Twice,
    _normalize,
)
from app.service import _cache as cache_alias
from app.service import __version__
from . import internal
from .internal import _value
from app import _private_module

if TYPE_CHECKING:
    from app.service import _counter

print(app.service._cache)
print(svc._normalize, svc.public, svc.__version__)
print(service._counter, internal._helper)
print(app.service._missing)


def persist(log: SessionLog, record: Record, frozen: Frozen):
    log._images.retain()
    log._images._retain()
    log._limit = 4
    log._count += 1
    del log._other
    record._secret
    record.log._images
    frozen._value
    return log._view


def annotated(
    first: "SessionLog",
    second: SessionLog | None,
    third: Annotated[SessionLog, "meta"],
    fourth: None | SessionLog,
):
    return first._images, second._images, third._images, fourth._images


def inherited(child: Child, grandchild: Grandchild, mixed: Mixed):
    return child._images, grandchild._own, grandchild._limit, mixed._images


def other_classes(decorated: Decorated, twice: Twice):
    return decorated._hidden, twice._x


def locals_flow(images: Images):
    log = SessionLog(images, None)
    log._images
    typed: SessionLog = make()
    typed._limit
    rebound = SessionLog(images, None)
    rebound = None
    rebound._images
    chained = log
    chained._made._retain()
    log._mixed
    log._reset
    log.shared._retain()
    log.maybe._retain()
    log.either._retain()
    log.reversed._retain()
    log._static()
    return [item._images for item in [log]], (lambda log: log._images)


def shadowed(log: SessionLog):
    log = None
    return log._images


def blocked(images: Images):
    images = None
    log = SessionLog(images, None)
    return log._images


class Holder:
    log: SessionLog

    def __init__(self, log: SessionLog):
        self.other = log

    def use(self):
        self.log._images
        self.other._limit
        self._own_private

    @staticmethod
    def static(log: SessionLog):
        return log._images

    @classmethod
    def build(cls, log: SessionLog):
        return cls._internal, log._limit


class Rebinding:
    def use(self, log: SessionLog):
        self = log
        return self._images


def nested():
    def inner(log: SessionLog):
        return log._images

    return inner


def closures():
    import app.service as local

    def inner():
        return local._cache

    class Inside:
        value = local._cache

        def method(self):
            return local._normalize

    return inner, Inside


def comprehension(items):
    return [svc._cache for svc in items], [app.service._cache for _ in items]


def star_scope():
    from os import *

    return svc._cache


def rebind_import():
    import app.service as twice
    import app.internal as twice

    return twice._helper
