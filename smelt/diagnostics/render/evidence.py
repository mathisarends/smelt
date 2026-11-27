from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smelt.diagnostics.violation import Violation

_PLACEHOLDER = re.compile(r"\{([\w.]+)\}")


def subject_name(violation: Violation, template: str) -> str:
    """``template`` with its braces filled from the finding's ``expected`` data.

    A key such as ``{class}`` or ``{evidence.type}`` names a value; the result is
    empty when the template names none or the finding lacks one of them.
    """
    values = [
        _text(_lookup(violation.expected, key))
        for key in _PLACEHOLDER.findall(template)
    ]
    if not values or not all(values):
        return ""
    filled = iter(values)
    return _PLACEHOLDER.sub(lambda _match: next(filled), template)


def decision(violation: Violation, keys: tuple[str, ...]) -> str:
    """The first of ``keys`` the finding carries, as ``Key: value``; else empty."""
    for key in keys:
        value = _text(_lookup(violation.expected, key))
        if value:
            title = key.rsplit(".", maxsplit=1)[-1].replace("_", " ").capitalize()
            return f"{title}: {value}"
    return ""


def _lookup(expected: Mapping[str, object] | None, path: str) -> object:
    value: object = expected
    for key in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _text(value: object) -> str:
    if value is None or isinstance(value, bool | Mapping):
        return ""
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value)
    return str(value)
