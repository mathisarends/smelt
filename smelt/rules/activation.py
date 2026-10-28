"""Typed, declarative activation metadata for configuration-driven rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from smelt.config.models import SmeltConfig


@dataclass(frozen=True, slots=True)
class PolicyActivation:
    description: str
    matches: Callable[[SmeltConfig], bool]
