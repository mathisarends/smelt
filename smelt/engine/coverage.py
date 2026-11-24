"""What a check covered and under which policy, for machine readers of a report.

A stored report alone should tell a clean inventory from a clean diff, and say
which boundaries were exempt or not analyzed at all.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import TYPE_CHECKING, Any

from smelt.analysis.workspace import workspace_members
from smelt.model import ModuleKind

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext
    from smelt.config.models import SmeltConfig


def analysis_facts(
    ctx: AnalysisContext,
    *,
    include_ordering: bool = False,
    include_test_quality: bool = False,
) -> dict[str, Any]:
    """What the analysis found about coverage; the native core reports the same."""
    inventory = ctx.coverage
    facts: dict[str, Any] = {
        **inventory.summary(),
        "inventory": [entry.to_json() for entry in inventory.entries],
        "modules": _classification(ctx),
        "contracts": ctx.contracts.describe(),
        "python_targets": ctx.python_targets.describe(),
        "omitted_workspace_members": _omitted_members(ctx),
    }
    if include_ordering and ctx.ordering.files:
        facts["ordering"] = ctx.ordering.summary()
    if include_test_quality and ctx.test_syntax.modules:
        facts["test_quality"] = {
            "files": len(ctx.test_syntax.modules),
            "collected_tests": ctx.test_syntax.collected_tests(),
        }
    return facts


def effective_coverage(
    config: SmeltConfig,
    facts: dict[str, Any],
    *,
    include_inventory: bool = False,
) -> dict[str, Any]:
    """The report's coverage: the analysis facts under the policy that produced them."""
    project = config.project
    architecture = config.architecture
    report = {
        "mode": "strict" if config.strict_coverage else "configured",
        "architecture_configured": bool(
            architecture.has_layout
            or any(member.governs_architecture for member in config.members.values())
        ),
        **{key: facts[key] for key in _SUMMARY},
        "policy_hash": policy_hash(config),
        "rules": config.model_dump(mode="json")["rules"],
        "root_packages": list(project.root_packages),
        "source_roots": list(project.source_roots),
        "test_roots": list(project.test_roots),
        "exclude": list(project.exclude_patterns),
        "exclusions": [
            entry if isinstance(entry, str) else entry.model_dump(mode="json")
            for entry in project.exclude
        ],
        "omitted_workspace_members": facts["omitted_workspace_members"],
        "member_architectures": {
            path: list(member.packages)
            for path, member in config.members.items()
            if member.governs_architecture
        },
        "member_boundaries": {
            pattern: policy.model_dump(mode="json")
            for pattern, policy in architecture.members.items()
        },
        "modules": facts["modules"],
        "exempt": {
            "composition_root": list(architecture.composition_root),
            "wiring": list(architecture.wiring),
        },
        "third_party": {
            name: layer.third_party.model_dump(mode="json")
            for name, layer in architecture.layers.items()
        },
        "imports": architecture.import_graph.model_dump(mode="json"),
        "cross_feature": {
            "default": architecture.cross_feature.default,
            "allow": architecture.cross_feature.entries(),
        },
        "contracts": facts["contracts"],
        "python_targets": facts["python_targets"],
        "tests": {
            **config.tests.model_dump(mode="json"),
            "layout": config.tests.layout,
            "mirror": config.tests.mirror,
            "mirror_exclude": list(config.tests.mirror_exclude),
        },
    }
    if include_inventory:
        report["inventory"] = facts["inventory"]
    if project.workspace == "uv":
        report["scope"] = "workspace"
    if "ordering" in facts:
        report["ordering"] = facts["ordering"]
    if "test_quality" in facts:
        report["test_quality"] = {
            "mode": "syntactic",
            **facts["test_quality"],
            "fixture_traversal": False,
            "fixture_return_annotations": True,
            "fixture_return_inference": "consistent known constructor/alias or fixed tuple; no execution",
            "operation_families": [
                "unittest.mock",
                "pytest-mock",
                "pytest.MonkeyPatch.setattr/delattr",
            ],
            "instance_provenance": [
                "constructor",
                "annotation",
                "fixture-return",
                "fixture-inferred",
            ],
        }
    return report


def policy_hash(config: SmeltConfig) -> str:
    """Equal for two reports checked under the same resolved config."""
    policy = config.model_dump(mode="json")
    # Neutral additive settings preserve policy hashes in released reports.
    if not config.structure.canonical_exports:
        policy["architecture"].pop("canonical_exports", None)
    if config.architecture.import_graph.type_checking_cycles == "include":
        policy["architecture"]["import_graph"].pop("type_checking_cycles", None)
    if not config.members:
        policy.pop("members", None)
    if not config.architecture.members:
        policy["architecture"].pop("members", None)
    if config.project.workspace is None:
        policy["project"].pop("workspace", None)
    architectures = [
        policy["architecture"],
        *(member["architecture"] for member in policy.get("members", {}).values()),
    ]
    for architecture in architectures:
        if architecture is not None:
            for layer in architecture["layers"].values():
                if not layer["modules"]:
                    layer.pop("modules")
    if len(config.rule_layers) > 1:
        policy["rule_layers"] = [
            {
                key: value if isinstance(value, str) else value.model_dump(mode="json")
                for key, value in layer.items()
            }
            for layer in config.rule_layers
        ]
    payload = json.dumps(policy, sort_keys=True)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


# The inventory summary, as `CoverageIndex.summary` reports it.
_SUMMARY = (
    "complete",
    "counts",
    "scan_roots",
    "unexplained_exclusions",
    "discovery_errors",
)


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
    configured = set(ctx.config.project.root_packages)
    analyzed = {
        directory
        for source in ctx.files.sources.values()
        for directory in ctx.files.resolve_path(source.absolute).parents
    }
    omitted: list[dict[str, Any]] = []
    for member in workspace_members(ctx.root, store=ctx.store):
        if member.skipped:
            omitted.append({"path": member.path, "reason": member.skipped})
            continue
        if member.source_root is None:
            continue
        missing = [name for name in member.packages if name not in configured]
        uncovered = [
            f"{member.source_root}/{name}"
            for name in member.packages
            if name in configured
            and ctx.files.resolve_path(ctx.root / member.source_root / name)
            not in analyzed
        ]
        reasons = []
        if missing:
            reasons.append("not in project.root_packages: " + ", ".join(missing))
        if uncovered:
            reasons.append("no analyzed source files in: " + ", ".join(uncovered))
        if reasons:
            omitted.append({"path": member.path, "reason": "; ".join(reasons)})
    return omitted
