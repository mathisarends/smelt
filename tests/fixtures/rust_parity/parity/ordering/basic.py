#!/usr/bin/env python
# -*- coding: utf-8 -*-
# ruff: noqa
"""Ordering basics."""
from __future__ import annotations

import os


def _helper():
    return 1


def build():
    return _helper()


__version__ = "1"
T = TypeVar("T")
LIMIT = 3


def _late():
    return LIMIT


if __name__ == "__main__":
    build()

import sys
x = 1
x += 2
a, b = 1, 2
del a
for item in range(3):
    pass
while False:
    break
with open(os.devnull) as handle, open(os.devnull) as (other):
    pass
try:
    pass
except Exception:
    pass
assert x, "message"
print((yield_ := 1))
type Alias[K: int, *Ts, **P] = dict[K, int]
match x:
    case _:
        pass
raise SystemExit(0) from None
