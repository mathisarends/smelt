from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.context import AnalysisContext
    from smelt.diagnostics.violation import Violation


class _EngineRule(Rule):
    """Meta rules are evaluated by the engine after filtering, not by ``check``."""

    category = Category.META
    scope = "project"
    enabled_by_default = True

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        return iter(())


class UnusedSuppression(_EngineRule):
    code = "MET001"
    name = "meta/unused-suppression"
    default_severity = Severity.WARNING
    doc = RuleDoc(
        summary="An inline suppression is unused or names an unknown identifier.",
        rationale=(
            "Stale suppressions hide future violations on the same line and make readers "
            "believe there is an exception where there is none."
            " Inline comments accept rule names, codes and code prefixes, like YAML and the CLI; exact pre-family SMT codes still resolve. Unknown identifiers, including retired rule names, are diagnosed with their replacement even in narrowed checks when MET001 is selected. Known inactive rules are not treated as unknown."
        ),
        bad="import os  # smelt: ignore[architecture/layer-boundary] -- legacy",
        good="import os",
        fix="Remove the comment, or the unused codes from it.",
        config=("suppressions.require_reason",),
    )


class SuppressionWithoutReason(_EngineRule):
    code = "MET002"
    name = "meta/missing-suppression-reason"
    default_severity = Severity.ERROR
    doc = RuleDoc(
        summary="An inline suppression without a `-- reason`.",
        rationale=(
            "Every exception to the architecture should explain itself, so the next "
            "person (or agent) knows whether it can be removed."
        ),
        bad="from app.infra import Db  # smelt: ignore[ARC001]",
        good="from app.infra import Db  # smelt: ignore[ARC001] -- migration tracked in #123",
        fix="Append ` -- <reason>` to the comment.",
        config=("suppressions.require_reason",),
    )


class ResolvedDebt(_EngineRule):
    code = "MET003"
    name = "meta/resolved-debt"
    default_severity = Severity.WARNING
    doc = RuleDoc(
        summary="A debt entry that no longer matches any violation.",
        rationale=(
            "Fixed violations should leave the debt file, otherwise they could silently "
            "come back."
        ),
        bad='{"code": "ARC001", "path": "app/application/old.py", ...}',
        good="(entry removed after the violation was fixed)",
        fix="Run `smelt debt --prune`.",
        config=("debt",),
    )


class MissingRulePolicy(_EngineRule):
    code = "MET004"
    name = "meta/missing-rule-policy"
    default_severity = Severity.WARNING
    doc = RuleDoc(
        summary="An explicitly enabled rule is missing the policy it needs in its scope.",
        rationale=(
            "An explicitly enabled rule can appear to enforce a convention even "
            "when its required policy does not reach the setting's scope. A rule "
            "named without any matching policy is a configuration error; this "
            "warning covers the narrower case. On full checks, it "
            "reports effective rule settings with no applicable public, "
            "min_imports_to_bundle, min_importers_to_export or import style policy "
            "for ARC007, IMP001, ARC008 or IMP003 respectively. It uses the declared "
            "policy and setting scope, independent of the number of source findings. "
            "Public and bundling contracts can govern callers across the project. "
            "An explicit public: [] is a policy. Changed, staged, and path-scoped "
            "checks skip this warning. CLI selection alone does not count as a setting."
        ),
        bad=(
            "style:\n  overrides:\n"
            "    api: {match: app.api, imports: {style: absolute}}\n"
            "    jobs: {match: app.jobs, rules: {imports/forbidden-import-style: error}}"
        ),
        good=(
            "style:\n  overrides:\n"
            "    api: {match: app.api, imports: {style: absolute}}\n"
            "    jobs:\n      match: app.jobs\n      imports: {style: relative}\n"
            "      rules: {imports/forbidden-import-style: error}"
        ),
        fix=(
            "Configure the named policy for the intended scope. Remove the explicit "
            "rule setting or set it to off where the convention should not apply."
        ),
        config=("rules", "style.imports", "style.overrides", "architecture.packages"),
        example_language="yaml",
    )
