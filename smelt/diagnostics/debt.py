from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from smelt.config.errors import ConfigError, ConfigIssue, format_loc

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from smelt.diagnostics.violation import Violation

DEBT_VERSION = 1


class _DebtItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    fingerprint: str
    code: str
    path: str | None = None
    message: str = ""


class _DebtDocument(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    version: Literal[1]
    violations: list[_DebtItem]


@dataclass(frozen=True, slots=True)
class DebtEntry:
    fingerprint: str
    code: str
    path: str | None
    message: str


def fingerprint(violation: Violation, snippet: str, *, legacy: bool = False) -> str:
    # An import finding is an architecture edge, independent of its spelling.
    # Multiplicity in Debt.match still detects an additional identical edge.
    normalized = (
        ""
        if violation.target_module and not legacy
        else re.sub(r"\s+", " ", snippet).strip()
    )
    payload = "\x1f".join(
        [
            violation.code,
            violation.path or "",
            violation.source_module or "",
            violation.target_module or "",
            normalized,
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _cycle_edges(violation: Violation) -> list[str]:
    if violation.code != "SMT104" or not violation.expected:
        return []
    return list(violation.expected.get("cyclic_edges", []))


def _fingerprints(violation: Violation, snippet: str) -> list[str]:
    edges = _cycle_edges(violation)
    if edges:
        # One baseline token per cyclic architecture edge, independent of the
        # displayed shortest witness. Subtracting edges is an improvement; adding
        # an edge or expanding/splitting a component cannot hide behind a witness.
        return [
            hashlib.sha256(f"SMT104\x1f{edge}".encode()).hexdigest()[:32]
            for edge in edges
        ]
    return [fingerprint(violation, snippet)]


@dataclass
class Debt:
    entries: list[DebtEntry]

    @classmethod
    def load(cls, path: Path) -> Debt:
        if not path.is_file():
            return cls([])
        try:
            data = _DebtDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise ConfigError(
                [ConfigIssue("", f"cannot read debt file: {exc}")], path.as_posix()
            ) from exc
        except ValidationError as exc:
            issues = [
                ConfigIssue(format_loc(e["loc"]), str(e["msg"])) for e in exc.errors()
            ]
            raise ConfigError(issues, path.as_posix()) from exc
        return cls(
            [
                DebtEntry(
                    fingerprint=item.fingerprint,
                    code=item.code,
                    path=item.path,
                    message=item.message,
                )
                for item in data.violations
            ]
        )

    @classmethod
    def from_violations(
        cls, violations: Iterable[Violation], snippet: Callable[[Violation], str]
    ) -> Debt:
        entries = [
            DebtEntry(key, v.code, v.path, v.message)
            for v in violations
            for key in _fingerprints(v, snippet(v))
        ]
        entries.sort(key=lambda e: (e.path or "", e.code, e.fingerprint))
        return cls(entries)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": DEBT_VERSION,
            "violations": [
                {
                    "fingerprint": e.fingerprint,
                    "code": e.code,
                    "path": e.path,
                    "message": e.message,
                }
                for e in self.entries
            ],
        }
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def match(
        self, violations: Iterable[Violation], snippet: Callable[[Violation], str]
    ) -> tuple[list[Violation], list[Violation], list[DebtEntry]]:
        """Split into (new, known) violations and the resolved entries."""
        available = Counter(entry.fingerprint for entry in self.entries)
        new: list[Violation] = []
        known: list[Violation] = []
        for violation in violations:
            text = snippet(violation)
            keys = _fingerprints(violation, text)
            matched: set[str] = set()
            for key in keys:
                if available[key] > 0:
                    available[key] -= 1
                    matched.add(key)
            if not matched and not _cycle_edges(violation):
                # Existing version-1 debt files used the complete source line.
                key = fingerprint(violation, text, legacy=True)
                if available[key] > 0:
                    available[key] -= 1
                    matched.add(keys[0])
            if matched:
                existing = violation
                if len(matched) < len(keys):
                    # Pruning may retain the old part of a growing cycle, but
                    # must never accept its newly introduced edges as debt.
                    existing = replace(
                        violation,
                        expected={
                            **(violation.expected or {}),
                            "cyclic_edges": [
                                edge
                                for edge, key in zip(
                                    _cycle_edges(violation), keys, strict=True
                                )
                                if key in matched
                            ],
                        },
                    )
                known.append(existing)
            if len(matched) < len(keys):
                new.append(violation)
        resolved: list[DebtEntry] = []
        for entry in self.entries:
            if available[entry.fingerprint] > 0:
                available[entry.fingerprint] -= 1
                resolved.append(entry)
        return new, known, resolved

    def without(self, resolved: Iterable[DebtEntry]) -> Debt:
        """A copy without ``resolved`` entries (matched by fingerprint, one per entry)."""
        remove = Counter(entry.fingerprint for entry in resolved)
        kept: list[DebtEntry] = []
        for entry in self.entries:
            if remove[entry.fingerprint] > 0:
                remove[entry.fingerprint] -= 1
            else:
                kept.append(entry)
        return Debt(kept)
