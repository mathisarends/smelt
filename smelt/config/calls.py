"""Match statically resolved calls in project-defined rules."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class CallMatcher:
    target: str
    layer: str | None = None

    def in_layer(self, layer: str) -> CallMatcher:
        return replace(self, layer=layer)


def to(target: str) -> CallMatcher:
    return CallMatcher(target)
