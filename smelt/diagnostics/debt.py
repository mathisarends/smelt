from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Annotated, Any, Literal, NamedTuple

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError

from smelt.config.errors import ConfigError, ConfigIssue, format_loc
from smelt.config.input_model import InputModel
from smelt.config.source import SourceStore
from smelt.diagnostics.legacy_codes import LEGACY_CODES, legacy_code

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from smelt.diagnostics.violation import Violation

_DEBT_VERSION = 2


class _DebtModel(InputModel):
    model_config = ConfigDict(strict=True)


class _DebtItem(_DebtModel):
    fingerprint: str
    code: str
    path: str | None = None
    message: str = ""


class _DebtDocument(_DebtModel):
    version: Literal[1]
    violations: list[_DebtItem]


class _DebtGroup(_DebtModel):
    code: str
    path: str | None = None
    message: str = ""
    scope: str | None = None
    fingerprints: list[str] = Field(min_length=1)


class _GroupedDebtDocument(_DebtModel):
    version: Literal[2]
    violations: list[_DebtGroup]


_DOCUMENT = TypeAdapter(
    Annotated[_DebtDocument | _GroupedDebtDocument, Field(discriminator="version")]
)


@dataclass(frozen=True, slots=True)
class DebtEntry:
    fingerprint: str
    code: str
    path: str | None
    message: str
    scope: str | None = None


def fingerprint(violation: Violation, snippet: str, *, legacy: bool = False) -> str:
    # An import finding is an architecture edge, independent of its spelling.
    # Multiplicity in Debt.match still detects an additional identical edge.
    normalized = (
        ""
        if violation.target_module and not legacy
        else re.sub(r"\s+", " ", snippet).strip()
    )
    # Fingerprints hash the pre-family SMT code so earlier baselines stay valid.
    payload = "\x1f".join(
        [
            legacy_code(violation.code),
            violation.path or "",
            violation.source_module or "",
            violation.target_module or "",
            normalized,
        ]
    )
    if violation.code == "TYP003" and not legacy and (scope := _scope(violation)):
        payload += f"\x1f{scope}"
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def _scope(violation: Violation) -> str | None:
    expected = violation.expected or {}
    value = expected.get("definition") or expected.get("scope")
    return value if isinstance(value, str) else None


def _fingerprints(violation: Violation, snippet: str) -> list[str]:
    edges = _semantic_edges(violation)
    if edges:
        # One baseline token per cyclic architecture edge, independent of the
        # displayed shortest witness. Subtracting edges is an improvement; adding
        # an edge or expanding/splitting a component cannot hide behind a witness.
        code = legacy_code(violation.code)
        prefix = (
            code
            if violation.code == "ARC004"
            else f"{code}\x1f{violation.path}\x1f{violation.source_module}"
        )
        return [
            hashlib.sha256(f"{prefix}\x1f{edge}".encode()).hexdigest()[:32]
            for edge in edges
        ]
    return [fingerprint(violation, snippet)]


def _semantic_edges(violation: Violation) -> list[str]:
    field = _edge_field(violation)
    if field is None or not violation.expected:
        return []
    return list(violation.expected.get(field, []))


def _edge_field(violation: Violation) -> str | None:
    return {
        "ARC004": "cyclic_edges",
        "ARC008": "export_edges",
        "IMP002": "import_edges",
        "LAY004": "ordering_edges",
        "IDM002": "dispatch_edges",
        "TYP005": "literal_edges",
        "MOD012": "pairing_edges",
        "IDM008": "lookup_edges",
        "CPX006": "comprehension_edges",
        "CPX007": "decision_edges",
        "VIS007": "definition_edges",
        "VIS011": "reexport_edges",
        "TST005": "patch_edges",
    }.get(violation.code)


class DebtMatch(NamedTuple):
    """Violations without debt, those debt accepts, and entries nothing matched."""

    new: list[Violation]
    known: list[Violation]
    resolved: list[DebtEntry]


class _EntryGroup(NamedTuple):
    path: str | None
    code: str
    scope: str | None
    message: str


@dataclass(slots=True)
class Debt:
    entries: list[DebtEntry]

    @classmethod
    def load(cls, path: Path, *, store: SourceStore | None = None) -> Debt:
        store = store or SourceStore()
        if not store.is_file(path):
            return cls([])
        try:
            data = _DOCUMENT.validate_json(store.read_text(path))
        except (OSError, UnicodeError) as exc:
            raise ConfigError(
                [ConfigIssue("", f"cannot read debt file: {exc}")], path.as_posix()
            ) from exc
        except ValidationError as exc:
            issues = [
                ConfigIssue(format_loc(e["loc"]), str(e["msg"])) for e in exc.errors()
            ]
            raise ConfigError(issues, path.as_posix()) from exc
        if isinstance(data, _GroupedDebtDocument):
            return cls(
                [
                    DebtEntry(
                        key, _current(item.code), item.path, item.message, item.scope
                    )
                    for item in data.violations
                    for key in item.fingerprints
                ]
            )
        return cls(
            [
                DebtEntry(
                    fingerprint=item.fingerprint,
                    code=_current(item.code),
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
            DebtEntry(key, v.code, v.path, v.message, _scope(v))
            for v in violations
            for key in _fingerprints(
                v, "" if v.target_module or _semantic_edges(v) else snippet(v)
            )
        ]
        entries.sort(key=lambda e: (e.path or "", e.code, e.fingerprint))
        return cls(entries)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2) + "\n", encoding="utf-8")

    def to_json(self) -> dict[str, Any]:
        """Group repeated metadata, retaining every matching token and its count."""
        groups: dict[_EntryGroup, dict[str, Any]] = {}
        for entry in self.entries:
            key = _EntryGroup(entry.path, entry.code, entry.scope, entry.message)
            if key not in groups:
                groups[key] = {
                    "code": entry.code,
                    "path": entry.path,
                    "message": entry.message,
                    **({"scope": entry.scope} if entry.scope is not None else {}),
                    "fingerprints": [],
                }
            groups[key]["fingerprints"].append(entry.fingerprint)
        return {
            "version": _DEBT_VERSION,
            "violations": [
                {
                    **groups[key],
                    "fingerprints": sorted(groups[key]["fingerprints"]),
                }
                for key in sorted(
                    groups,
                    key=lambda key: (
                        key.path is not None,
                        key.path or "",
                        key.code,
                        key.scope is not None,
                        key.scope or "",
                        key.message,
                    ),
                )
            ],
        }

    def match(
        self, violations: Iterable[Violation], snippet: Callable[[Violation], str]
    ) -> DebtMatch:
        """Split into (new, known) violations and the resolved entries."""
        available = Counter(entry.fingerprint for entry in self.entries)
        new: list[Violation] = []
        known: list[Violation] = []
        for violation in violations:
            text = (
                ""
                if violation.target_module or _semantic_edges(violation)
                else snippet(violation)
            )
            keys = _fingerprints(violation, text)
            matched: set[str] = set()
            for key in keys:
                if available[key] > 0:
                    available[key] -= 1
                    matched.add(key)
            if (
                not matched
                and not _semantic_edges(violation)
                and not (violation.code == "TYP003" and _scope(violation))
            ):
                # Existing version-1 debt files used the complete source line.
                if violation.target_module:
                    text = snippet(violation)
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
                            (_edge_field(violation) or "cyclic_edges"): [
                                edge
                                for edge, key in zip(
                                    _semantic_edges(violation), keys, strict=True
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
        return DebtMatch(new, known, resolved)


def _current(code: str) -> str:
    """The family code of an entry written with a pre-family SMT code."""
    return LEGACY_CODES.get(code, code)
