"""Independent architecture drafts under one shared uv workspace policy."""

from __future__ import annotations

import posixpath
import tomllib
from typing import TYPE_CHECKING

import yaml

from smelt.analysis.errors import AnalysisError
from smelt.config.loader import CONFIG_FILENAMES
from smelt.engine.inference import InferredConfig, infer_config, render_config
from smelt.engine.workspace import workspace_members

if TYPE_CHECKING:
    from pathlib import Path


def workspace_drafts(
    root: Path,
    *,
    root_filename: str,
    maintainability: bool = False,
    member: str | None = None,
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
        data = yaml.safe_load(render_config(base, maintainability=maintainability))
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
    for path in members:
        if selected is not None and path != selected:
            continue
        inferred = infer_config(root / path, include_members=False)
        if inferred is None:
            continue
        data = yaml.safe_load(render_config(inferred))
        architecture = data.get("architecture", {})
        architecture.pop("import_graph", None)
        filename = next(
            (name for name in CONFIG_FILENAMES if (root / path / name).exists()),
            CONFIG_FILENAMES[0],
        )
        schema = posixpath.relpath(".smelt.schema.json", path)
        policy = (
            "# This architecture replaces the root architecture for this member. Review the inferred boundaries.\n"
            + yaml.safe_dump({"architecture": architecture}, sort_keys=False)
            if architecture
            else "# No member architecture was inferred. This member inherits the root architecture.\n{}\n"
        )
        drafts[posixpath.join(path, filename)] = (
            f"# yaml-language-server: $schema={schema}\n" + policy
        )
    return drafts
