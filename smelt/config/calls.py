"""Match statically resolved calls in project-defined rules."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class CallMatcher:
    target: str
    layer: str | None = None

    def __post_init__(self) -> None:
        _require_target(self.target)
        _require_layer(self.layer)

    def in_layer(self, layer: str) -> CallMatcher:
        return replace(self, layer=layer)


def to(  # smelt: ignore[VIS009] -- Public API that project rule modules call as calls.to(...).
    target: str,
) -> CallMatcher:
    return CallMatcher(target)


def _require_target(target: object) -> None:
    if not (
        isinstance(target, str)
        and "." in target
        and all(part.isidentifier() for part in target.split("."))
    ):
        raise ValueError(
            f"expected a qualified call target such as 'package.function', got {target!r}"
        )


def _require_layer(layer: object) -> None:
    if layer is not None and not (isinstance(layer, str) and layer.strip()):
        raise ValueError(f"expected a layer name, got {layer!r}")
