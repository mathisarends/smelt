"""What analyzing a project yields before debt, scope and comparison apply."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from smelt.diagnostics.violation import Violation


@dataclass(slots=True)
class Analysis:
    violations: list[Violation]
    suppressed: int
    coverage: dict[str, Any]
    modules: int
    paths: list[str]
