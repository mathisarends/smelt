"""What a check covered and under which policy, for machine readers of a report.

A stored report alone should tell a clean inventory from a clean diff, and say
which boundaries were exempt or not analyzed at all.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from smelt.config.models import SmeltConfig
    from smelt.config.project_rules import Rule


def effective_coverage(
    config: SmeltConfig,
    facts: dict[str, Any],
    *,
    include_inventory: bool = False,
    project_rules: tuple[Rule, ...] = (),
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
        "policy_hash": policy_hash(config, project_rules=project_rules),
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


def policy_hash(config: SmeltConfig, *, project_rules: tuple[Rule, ...] = ()) -> str:
    """Equal for two reports checked under the same resolved config."""
    policy = config.model_dump(mode="json")
    if project_rules:
        policy["project_rules"] = [asdict(rule) for rule in project_rules]
    if not config.plugins:
        policy.pop("plugins", None)
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
