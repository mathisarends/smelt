"""A verified lazy-export table: its names resolve when a parameter binds them once."""

from importlib import import_module

_EXPORTS = {
    "Widget": ("parity.lazy.widgets", "Widget"),
    "helper": ("parity.lazy.widgets", "helper"),
    "_Hidden": ("parity.lazy.widgets", "Hidden"),
}
__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))


def consumer(Widget, helper):
    first = Widget().render()
    second = helper().render()
    return first, second
