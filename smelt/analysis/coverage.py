"""The policy coverage of each Python file, as the native core inventories it."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from smelt.model import ModuleKind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from smelt.model import ModuleInfo

type CoverageStatus = Literal[
    "governed", "facade", "unclassified", "uncovered", "excluded"
]


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    path: str
    module: str | None
    status: CoverageStatus
    kind: str | None = None
    feature: str | None = None
    layer: str | None = None
    wiring: bool = False
    central: bool = False
    reason: str | None = None
    policies: tuple[str, ...] = ()
    source_root: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "module": self.module,
            "status": self.status,
            "kind": self.kind,
            "feature": self.feature,
            "layer": self.layer,
            "wiring": self.wiring,
            "central": self.central,
            "reason": self.reason,
            "policies": list(self.policies),
            "source_root": self.source_root,
        }


@dataclass(frozen=True, slots=True)
class CoverageIndex:
    entries: tuple[CoverageEntry, ...]
    scan_roots: tuple[str, ...]
    unexplained_exclusions: tuple[str, ...]
    discovery_errors: tuple[str, ...] = ()

    @classmethod
    def from_report(cls, coverage: Mapping[str, Any]) -> CoverageIndex:
        """The inventory of a report's coverage, which the native core classified."""
        return cls(
            tuple(
                CoverageEntry(
                    entry["path"],
                    entry["module"],
                    entry["status"],
                    kind=entry["kind"],
                    feature=entry["feature"],
                    layer=entry["layer"],
                    wiring=entry["wiring"],
                    central=entry["central"],
                    reason=entry["reason"],
                    policies=tuple(entry["policies"]),
                    source_root=entry["source_root"],
                )
                for entry in coverage["inventory"]
            ),
            tuple(coverage["scan_roots"]),
            tuple(coverage["unexplained_exclusions"]),
            tuple(coverage["discovery_errors"]),
        )

    @property
    def gaps(self) -> tuple[CoverageEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.status in ("unclassified", "uncovered")
        )

    @property
    def complete(self) -> bool:
        return (
            not self.gaps
            and not self.unexplained_exclusions
            and not self.discovery_errors
        )

    def for_path(self, path: str) -> CoverageEntry | None:
        return next((entry for entry in self.entries if entry.path == path), None)

    def summary(self) -> dict[str, Any]:
        counts = Counter(entry.status for entry in self.entries)
        return {
            "complete": self.complete,
            "counts": {
                status: counts[status]
                for status in (
                    "governed",
                    "facade",
                    "unclassified",
                    "uncovered",
                    "excluded",
                )
            },
            "scan_roots": list(self.scan_roots),
            "unexplained_exclusions": list(self.unexplained_exclusions),
            "discovery_errors": list(self.discovery_errors),
        }


def is_governed(info: ModuleInfo | None, *, layers_declared: bool = True) -> bool:
    return bool(
        info
        and (
            info.layer
            or (info.feature is not None and not layers_declared)
            or info.wiring
            or info.kind in (ModuleKind.SHARED, ModuleKind.COMPOSITION_ROOT)
        )
    )
