"""Findings that share one decision or fix, for the JSON ``groups`` summary.

86 findings are rarely 86 problems: one facade re-exporting an adapter, one
missing cross-feature allowance or one old test directory explains many of them.
A group only points at findings; every finding stays in ``violations``.
"""

from __future__ import annotations

import posixpath
from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from smelt.diagnostics.violation import Violation

_CYCLE = "SMT104"
_TEST_LOCATION = "SMT401"


def is_transitive(violation: Violation) -> bool:
    """An import finding reached through other modules, not the import itself."""
    return violation.code != _CYCLE and len(violation.import_chain) > 1


def group_findings(violations: list[Violation]) -> list[dict[str, Any]]:
    """Facades, dependency edges, cycles and test moves behind several findings.

    Edges and test moves need two findings to form a group; a facade or a cycle
    is one decision even behind a single finding.
    """
    groups = [
        *_groups("facade", violations, _facade, minimum=1),
        *_groups("edge", violations, _edge, minimum=2),
        *_groups("cycle", violations, _cycle, minimum=1),
        *_groups("test_move", violations, _test_move, minimum=2),
    ]
    return sorted(groups, key=lambda g: (-g["count"], g["kind"], g["key"]))


def _groups(
    kind: str,
    violations: list[Violation],
    key: Callable[[Violation], str | None],
    *,
    minimum: int,
) -> list[dict[str, Any]]:
    members: dict[str, list[Violation]] = defaultdict(list)
    for violation in violations:
        value = key(violation)
        if value is not None:
            members[value].append(violation)
    return [
        _group(kind, value, found)
        for value, found in members.items()
        if len(found) >= minimum
    ]


def _group(kind: str, key: str, found: list[Violation]) -> dict[str, Any]:
    group: dict[str, Any] = {
        "kind": kind,
        "key": key,
        "count": len(found),
        "codes": sorted({v.code for v in found}),
        "violations": [v.finding_id for v in found],
    }
    if kind in ("facade", "edge"):
        transitive = sum(is_transitive(v) for v in found)
        group["direct"] = len(found) - transitive
        group["transitive"] = transitive
    if kind == "facade":
        group["edges"] = sorted({v.edge for v in found if v.edge})
    if kind == "cycle":
        first = found[0]
        group["witness"] = [link.to_json() for link in first.import_chain]
        group["type_checking_only"] = bool(
            first.expected and first.expected.get("type_checking_only")
        )
    return group


def _facade(violation: Violation) -> str | None:
    """The module relaying a transitive import: narrowing it fixes the chain."""
    if not is_transitive(violation):
        return None
    return violation.import_chain[0].imported


def _edge(violation: Violation) -> str | None:
    if violation.code == _CYCLE or not violation.edge:
        return None
    return f"{violation.code} {violation.edge}"


def _cycle(violation: Violation) -> str | None:
    return violation.edge if violation.code == _CYCLE and violation.edge else None


def _test_move(violation: Violation) -> str | None:
    if violation.code != _TEST_LOCATION or not violation.path:
        return None
    target = (violation.expected or {}).get("path")
    if not isinstance(target, str):
        return None
    return f"{posixpath.dirname(violation.path)}/ -> {posixpath.dirname(target)}/"
