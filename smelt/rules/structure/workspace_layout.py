from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext
    from smelt.rules.base import GuideTarget


class WorkspaceLayoutConvention(Rule):
    code = "LAY013"
    name = "layout/workspace-layout-convention"
    category = Category.LAYOUT
    scope = "project"
    activation = POLICIES["LAY013"]
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A top-level package deviates from the uv workspace's folder layout.",
        rationale=(
            "One workspace-wide layout makes package locations predictable. "
            "Choose flat for member/member or src for member/src/member. Smelt "
            "checks the top-level packages selected by its source roots and package "
            "discovery, including selected namespace packages. The last directory "
            "of the member path gives the expected package name. For the workspace "
            "root's own project, use its project.name from pyproject.toml. Names "
            "are lowercased and runs of hyphens, dots and underscores become one "
            "underscore."
        ),
        bad="# With project.workspace_layout: src\nservices/backend/backend/__init__.py",
        good="# With project.workspace_layout: src\nservices/backend/src/backend/__init__.py",
        fix=(
            "Move the package to the expected directory or align the member "
            "directory name with its intended package. Update build configuration, "
            "Smelt source roots and package selection, workspace globs, and any "
            "imports affected by a package rename. Review existing destination files "
            "before moving code."
        ),
        canonical="Use the workspace's canonical layout for top-level packages",
        config=(
            "project.workspace",
            "project.workspace_layout",
            "project.source_roots",
            "project.root_packages",
        ),
        example_language="text",
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        del target
        project = ctx.config.project
        if project.workspace_layout == "src":
            return "Put each member's top-level package in <member>/src/<member>"
        if project.workspace_layout == "flat":
            return "Put each member's top-level package in <member>/<member>"
        return None
