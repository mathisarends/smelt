"""Independent architecture drafts under one shared uv workspace policy."""

from __future__ import annotations

import posixpath
import tomllib
from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple

import yaml

from smelt.analysis import native
from smelt.analysis.errors import AnalysisError
from smelt.analysis.workspace import workspace_members
from smelt.config.errors import ConfigError
from smelt.config.loader import CONFIG_FILENAMES, parse_config
from smelt.config.patterns import path_matches
from smelt.engine.inference import (
    InferredConfig,
    infer_config,
    maintainability_rules,
    policy_rules,
    render_config,
)

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from smelt.config.models import SmeltConfig


def workspace_drafts(
    root: Path,
    *,
    root_filename: str,
    maintainability: bool = False,
    member: str | None = None,
    workspace: InferredConfig | None = None,
) -> dict[str, str]:
    """Render first; callers validate every output before writing any of them."""
    members = {
        item.path: item for item in workspace_members(root) if item.skipped is None
    }
    selected = None if member is None else posixpath.normpath(member.replace("\\", "/"))
    if selected is not None and selected not in members:
        raise AnalysisError(f'"{member}" is not a declared Python workspace member')
    drafts: dict[str, str] = {}
    if selected is None:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        inferred = (
            infer_config(root, include_members=False) if "project" in project else None
        )
        base = inferred or InferredConfig([], [], ["tests"])
        if workspace is not None:
            base = replace(base, mirror=workspace.mirror)
        data = yaml.safe_load(render_config(base))
        data.pop("rules", None)
        data["project"].pop("source_roots", None)
        data["project"]["workspace"] = "uv"
        data["project"]["test_roots"] = ["tests", "test"]
        if inferred is None:
            data.pop("architecture", None)
        drafts[root_filename] = (
            "# yaml-language-server: $schema=.smelt.schema.json\n"
            "# Shared style and member boundaries live here; member architectures live in their own smelt.yml.\n"
            "# Member rules that lower inherited severity need {severity: ..., reason: '...'}.\n"
            + yaml.safe_dump(data, sort_keys=False)
        )
        patterns = (
            project.get("tool", {})
            .get("uv", {})
            .get("workspace", {})
            .get("members", [])
        )
        draft_patterns = (["."] if inferred is not None else []) + [
            pattern
            for pattern in patterns
            if any(path_matches(pattern, path) for path in members)
        ]
        drafts[root_filename] = _with_boundary_draft(
            root, drafts[root_filename], data=data, patterns=draft_patterns
        )
    architectures: list[dict[str, Any]] = []
    for path in members:
        if selected is not None and path != selected:
            continue
        inferred = infer_config(root / path, include_members=False)
        if inferred is None:
            continue
        data = yaml.safe_load(render_config(inferred))
        architecture = data.get("architecture", {})
        architecture.pop("import_graph", None)
        for layer in architecture.get("layers", {}).values():
            layer.pop("third_party", None)
        if inferred.layer_gaps:
            for key in ("layers", "features", "modules", "cross_feature"):
                architecture.pop(key, None)
        filename = next(
            (name for name in CONFIG_FILENAMES if (root / path / name).exists()),
            CONFIG_FILENAMES[0],
        )
        architectures.append({"architecture": architecture})
        schema = posixpath.relpath(".smelt.schema.json", path)
        policy = (
            "# This file defines the internal architecture for this member. Review the inferred boundaries.\n"
            + yaml.safe_dump({"architecture": architecture}, sort_keys=False)
            if architecture
            else "# No member architecture was inferred. Declare local layers here when needed.\n{}\n"
        )
        drafts[posixpath.join(path, filename)] = (
            f"# yaml-language-server: $schema={schema}\n" + policy
        )
        if inferred.layer_gaps:
            missing = ", ".join(inferred.layer_gaps)
            drafts[posixpath.join(path, filename)] += (
                f"# Layer draft omitted. Review unclassified placements: {missing}.\n"
            )
    if selected is None:
        drafts[root_filename] += _root_rules(
            drafts[root_filename], architectures, maintainability=maintainability
        )
    return drafts


def _root_rules(
    document: str, architectures: list[dict[str, Any]], *, maintainability: bool
) -> str:
    """Member architectures are checked by the root's rules."""
    policies = policy_rules(yaml.safe_load(document), *architectures)
    if maintainability:
        return "\n" + "\n".join(maintainability_rules(policies)) + "\n"
    if not policies:
        return ""
    return "# Policies are checked only by the rules named here.\nrules:\n" + "".join(
        f"  {entry}\n" for entry in policies
    )


def _with_boundary_draft(
    root: Path, document: str, *, data: dict[str, Any], patterns: list[str]
) -> str:
    if not patterns:
        return document
    example = _boundary_draft(root, data, patterns)
    if not data.get("architecture"):
        return document + example
    comment = "\n".join(
        "  # " + line.removeprefix("#   ").removeprefix("# ")
        for line in example.splitlines()
        if line and line != "# architecture:"
    )
    return document.replace("\narchitecture:\n", f"\narchitecture:\n{comment}\n", 1)


def _boundary_draft(root: Path, data: dict[str, Any], patterns: list[str]) -> str:
    try:
        config, _ = parse_config(data, root=root)
        edges = _observed_edges(root, config)
    except (AnalysisError, ConfigError):
        # A draft remains useful when imports could not be analyzed.
        edges = []
        observations = ["# Observed imports are unavailable."]
    else:
        observations = [f"#   {source} -> {target}" for source, target in edges]
        if observations:
            observations.insert(0, "# Observed cross-member imports:")
    example = yaml.safe_dump(
        {
            "architecture": {
                "members": {
                    pattern: {
                        "may_depend_on": sorted(
                            {
                                target
                                for source, target in edges
                                if path_matches(pattern, source)
                            }
                        )
                    }
                    for pattern in patterns
                }
            }
        },
        sort_keys=False,
    )
    return "\n".join(
        [
            "",
            "# Review which members may depend on each other before enabling this example.",
            *observations,
            *[f"# {line}" for line in example.splitlines()],
            "",
        ]
    )


class _MemberEdge(NamedTuple):
    """An import from one workspace member into another."""

    source: str
    target: str


def _observed_edges(root: Path, config: SmeltConfig) -> list[_MemberEdge]:
    """The imports between workspace members, as the native core resolves them."""
    project = config.project
    report = native.request(
        "--internal-files",
        {
            "root": str(root.resolve()),
            "root_packages": project.root_packages,
            "source_roots": project.source_roots,
            "test_roots": project.test_roots,
            "exclude": list(project.exclude_patterns),
            "yaml": False,
            "member_edges": True,
        },
    )
    if "error" in report:
        raise AnalysisError(report["error"])
    return [_MemberEdge(source, target) for source, target in report["member_edges"]]
