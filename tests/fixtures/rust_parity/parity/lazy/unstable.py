"""A table that is rewritten at runtime proves nothing."""

import importlib

_EXPORTS = {"Widget": ("parity.lazy.widgets", "Widget")}
_EXPORTS["Other"] = ("parity.lazy.widgets", "Hidden")


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, attribute = _EXPORTS[name]
    value = getattr(importlib.import_module(module), attribute)
    return value


def consumer(Widget):
    return Widget().render()
