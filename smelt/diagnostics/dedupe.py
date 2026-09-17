from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smelt.diagnostics.violation import Violation

# winner -> codes it makes redundant when both report the same root cause
SPECIFICITY: dict[str, frozenset[str]] = {
    "SMT106": frozenset({"SMT103"}),
    "SMT101": frozenset({"SMT201", "SMT202"}),
    "SMT204": frozenset({"SMT303"}),
    "SMT402": frozenset({"SMT403"}),
}


def _key(violation: Violation) -> tuple[str | None, str | int | None]:
    if violation.target_module:
        return violation.path, violation.target_module
    return violation.path, violation.line


def deduplicate(violations: list[Violation]) -> list[Violation]:
    """Drop violations that a more specific rule already reports for the same cause."""
    codes_by_key: dict[tuple[str | None, str | int | None], set[str]] = defaultdict(set)
    lines_by_key: dict[tuple[str | None, int | None], set[str]] = defaultdict(set)
    for violation in violations:
        codes_by_key[_key(violation)].add(violation.code)
        lines_by_key[(violation.path, violation.line)].add(violation.code)

    kept: list[Violation] = []
    for violation in violations:
        same_cause = (
            codes_by_key[_key(violation)]
            | lines_by_key[(violation.path, violation.line)]
        )
        redundant = any(
            violation.code in losers and winner in same_cause
            for winner, losers in SPECIFICITY.items()
        )
        if not redundant:
            kept.append(violation)
    return kept
