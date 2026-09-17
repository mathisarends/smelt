import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from smelt.diagnostics.violation import Violation

BASELINE_VERSION = 1


@dataclass(frozen=True, slots=True)
class BaselineEntry:
    fingerprint: str
    code: str
    path: str | None
    message: str


def fingerprint(violation: Violation, snippet: str) -> str:
    normalized = re.sub(r"\s+", " ", snippet).strip()
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


@dataclass
class Baseline:
    entries: list[BaselineEntry]

    @classmethod
    def load(cls, path: Path) -> Baseline:
        if not path.is_file():
            return cls([])
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            [
                BaselineEntry(
                    fingerprint=item["fingerprint"],
                    code=item["code"],
                    path=item.get("path"),
                    message=item.get("message", ""),
                )
                for item in data.get("violations", [])
            ]
        )

    @classmethod
    def from_violations(
        cls, violations: Iterable[Violation], snippet: Callable[[Violation], str]
    ) -> Baseline:
        entries = [
            BaselineEntry(fingerprint(v, snippet(v)), v.code, v.path, v.message)
            for v in violations
        ]
        entries.sort(key=lambda e: (e.path or "", e.code, e.fingerprint))
        return cls(entries)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": BASELINE_VERSION,
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
    ) -> tuple[list[Violation], list[Violation], list[BaselineEntry]]:
        """Split into (new, baselined) violations and the stale entries."""
        available = Counter(entry.fingerprint for entry in self.entries)
        new: list[Violation] = []
        known: list[Violation] = []
        for violation in violations:
            key = fingerprint(violation, snippet(violation))
            if available[key] > 0:
                available[key] -= 1
                known.append(violation)
            else:
                new.append(violation)
        stale: list[BaselineEntry] = []
        for entry in self.entries:
            if available[entry.fingerprint] > 0:
                available[entry.fingerprint] -= 1
                stale.append(entry)
        return new, known, stale

    def without(self, stale: Iterable[BaselineEntry]) -> Baseline:
        """A copy without ``stale`` entries (matched by fingerprint, one per entry)."""
        remove = Counter(entry.fingerprint for entry in stale)
        kept: list[BaselineEntry] = []
        for entry in self.entries:
            if remove[entry.fingerprint] > 0:
                remove[entry.fingerprint] -= 1
            else:
                kept.append(entry)
        return Baseline(kept)
