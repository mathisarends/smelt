"""Expose a project's declarations through the same catalog as built-in rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import RuleDoc

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext
    from smelt.config.project_rules import Rule as Declaration
    from smelt.rules.base import GuideTarget


class ProjectRule:
    """Catalog entry for a loaded declaration."""

    category = Category.PROJECT
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})

    def __init__(self, declaration: Declaration) -> None:
        self._declaration = declaration
        self.code = declaration.name
        self.name = declaration.name

    def explain(self) -> RuleDoc:
        rule = self._declaration
        # A project rule states a finding and a guide; it has no separate
        # rationale or preferred example, so those sections stay empty.
        return RuleDoc(
            summary=rule.message,
            rationale="",
            bad=f"{rule.match.target}()",
            good="",
            fix=rule.guide,
            canonical=rule.guide,
            config=("rules",),
        )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        layer = self._declaration.match.layer
        if layer is None or (target.info and target.info.layer == layer):
            return self._declaration.guide
        return None
