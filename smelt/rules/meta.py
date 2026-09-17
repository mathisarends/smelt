from typing import TYPE_CHECKING

from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.context import AnalysisContext
    from smelt.diagnostics.violation import Violation


class _EngineRule(BaseRule):
    """Meta rules are evaluated by the engine after filtering, not by ``check``."""

    category = Category.META

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        return iter(())


class UnusedSuppression(_EngineRule):
    code = "SMT901"
    name = "unused-suppression"
    default_severity = Severity.WARNING
    fixable = True
    doc = RuleDoc(
        summary="An inline `# smelt: ignore[...]` comment that suppresses nothing.",
        rationale=(
            "Stale suppressions hide future violations on the same line and make readers "
            "believe there is an exception where there is none."
        ),
        bad="import os  # smelt: ignore[SMT101] -- legacy",
        good="import os",
        fix="Remove the comment, or the unused codes from it. `smelt fix SMT901` does this.",
        config=("suppressions.require_reason",),
    )


class SuppressionWithoutReason(_EngineRule):
    code = "SMT902"
    name = "suppression-without-reason"
    default_severity = Severity.ERROR
    doc = RuleDoc(
        summary="An inline suppression without a `-- reason`.",
        rationale=(
            "Every exception to the architecture should explain itself, so the next "
            "person (or agent) knows whether it can be removed."
        ),
        bad="from app.infra import Db  # smelt: ignore[SMT101]",
        good="from app.infra import Db  # smelt: ignore[SMT101] -- migration tracked in #123",
        fix="Append ` -- <reason>` to the comment.",
        config=("suppressions.require_reason",),
    )


class StaleBaseline(_EngineRule):
    code = "SMT903"
    name = "stale-baseline"
    default_severity = Severity.WARNING
    doc = RuleDoc(
        summary="A baseline entry that no longer matches any violation.",
        rationale=(
            "Fixed violations should leave the baseline, otherwise they could silently "
            "come back."
        ),
        bad='{"code": "SMT101", "path": "app/application/old.py", ...}',
        good="(entry removed after the violation was fixed)",
        fix="Run `smelt baseline --prune`.",
        config=("baseline",),
    )
