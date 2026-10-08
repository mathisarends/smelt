"""What a check covered and under which policy, for machine readers of a report.

A stored report alone should tell a clean inventory from a clean diff, and say
which boundaries were exempt or not analyzed at all.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import TYPE_CHECKING, Any

from smelt.engine.workspace import workspace_members
from smelt.model import ModuleKind

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext
    from smelt.config.models import SmeltConfig


def policy_hash(config: SmeltConfig) -> str:
    """Equal for two reports checked under the same resolved config."""
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def effective_coverage(ctx: AnalysisContext) -> dict[str, Any]:
    config = ctx.config
    project = config.project
    architecture = config.architecture
    return {
        "policy_hash": policy_hash(config),
        "root_packages": list(project.root_packages),
        "source_roots": list(project.source_roots),
        "test_roots": list(project.test_roots),
        "exclude": list(project.exclude),
        "omitted_workspace_members": _omitted_members(ctx),
        "modules": _classification(ctx),
        "exempt": {
            "composition_root": list(architecture.composition_root),
            "wiring": list(architecture.wiring),
        },
        "third_party": {
            name: layer.third_party.model_dump(mode="json")
            for name, layer in architecture.layers.items()
        },
        "imports": architecture.imports.model_dump(mode="json"),
        "cross_feature": {
            "default": architecture.cross_feature.default,
            "allow": architecture.cross_feature.entries(),
        },
        "tests": {
            "layout": config.tests.layout,
            "mirror": config.tests.mirror,
            "unmirrored": list(config.tests.unmirrored),
        },
    }


def _classification(ctx: AnalysisContext) -> dict[str, int]:
    """Source modules by architecture kind; wiring and central ones also counted apart."""
    counts: Counter[str] = Counter({kind.value: 0 for kind in ModuleKind})
    wiring = central = 0
    for module in ctx.files.sources:
        info = ctx.model.info(module)
        kind = info.kind if info else ModuleKind.UNCLASSIFIED
        counts[kind.value] += 1
        wiring += bool(info and info.wiring)
        central += bool(info and info.central)
    return {
        "total": len(ctx.files.sources),
        **dict(counts),
        "wiring": wiring,
        "central": central,
    }


def _omitted_members(ctx: AnalysisContext) -> list[dict[str, Any]]:
    """Workspace members, declared next to the config, whose code is not analyzed."""
    analyzed = set(ctx.config.project.root_packages)
    omitted: list[dict[str, Any]] = []
    for member in workspace_members(ctx.root):
        missing = [name for name in member.packages if name not in analyzed]
        if member.skipped:
            omitted.append({"path": member.path, "reason": member.skipped})
        elif missing:
            omitted.append(
                {
                    "path": member.path,
                    "reason": "not in project.root_packages: " + ", ".join(missing),
                }
            )
    return omitted
