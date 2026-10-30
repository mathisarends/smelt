from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smelt.diagnostics.violation import Violation

# winner -> codes it makes redundant when both report the same root cause
SPECIFICITY: dict[str, frozenset[str]] = {
    "SMT306": frozenset({"SMT301", "SMT305"}),
    # shared or central code importing a feature (also its layer or wiring module):
    # the feature import is the root cause
    "SMT105": frozenset({"SMT101", "SMT106"}),
}


def _key(violation: Violation) -> tuple[str | None, str | int | None]:
    if violation.target_module:
        return violation.path, violation.target_module
    return violation.path, violation.line


def deduplicate(violations: list[Violation]) -> list[Violation]:
    """Drop violations that a more specific rule already reports for the same cause."""
    codes_by_key: dict[tuple[str | None, str | int | None], set[str]] = defaultdict(set)
    for violation in violations:
        codes_by_key[_key(violation)].add(violation.code)

    kept: list[Violation] = []
    for violation in violations:
        # One originating import can reach several unrelated forbidden targets.
        # Only a finding for the same target can explain an import violation.
        same_cause = codes_by_key[_key(violation)]
        redundant = any(
            violation.code in losers and winner in same_cause
            for winner, losers in SPECIFICITY.items()
        )
        if not redundant:
            kept.append(violation)
    return kept
