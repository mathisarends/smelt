from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def readable_list(items: Iterable[str], *, conjunction: str = "and") -> str:
    """Join names for prose: ``a``, ``a and b``, ``a, b and c``."""
    *leading, last = list(items) or [""]
    return f"{', '.join(leading)} {conjunction} {last}" if leading else last
