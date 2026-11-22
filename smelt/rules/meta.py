from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.diagnostics.fixes import Applicability, Fix, FixAvailability, TextEdit
from smelt.diagnostics.suppressions import DIRECTIVE, is_rule_name
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.edits import SourceText
from smelt.rules.renames import replacement_selectors

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from smelt.analysis.context import AnalysisContext
    from smelt.diagnostics.suppressions import Suppression
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
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="An inline suppression is unused or names an unknown identifier.",
        rationale=(
            "Stale suppressions hide future violations on the same line and make readers "
            "believe there is an exception where there is none."
            " Inline comments accept rule names, codes and code prefixes, like YAML and the CLI. Unknown identifiers, including retired rule names, are diagnosed with their replacement even in narrowed checks when MET001 is selected. Known inactive rules are not treated as unknown."
        ),
        bad="import os  # smelt: ignore[architecture/layer-boundary] -- legacy",
        good="import os",
        fix=(
            "Remove the comment, or the unused codes from it. check --fix removes "
            "unused suppressions and unused identifiers, keeping the reason while "
            "other identifiers remain, and replaces retired rule names and codes "
            "with their successors. Other unknown identifiers carry a suggested "
            "removal that --fix never applies: correct a misspelled identifier "
            "instead where the comment is still needed."
        ),
        canonical="Remove suppressions that no longer match a finding",
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
        canonical="Give every smelt suppression comment a -- reason",
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
        canonical="Prune resolved debt with smelt debt --prune",
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
        canonical="Configure the policy that an explicitly enabled rule needs",
        config=("rules", "style.imports", "style.overrides", "architecture.packages"),
        example_language="yaml",
    )


class UncoveredPackageFacade(_EngineRule):
    code = "MET005"
    name = "meta/uncovered-package-facade"
    default_severity = Severity.HINT
    doc = RuleDoc(
        summary="A package facade cannot supply names that IMP004 is enabled to check.",
        rationale=(
            "imports/prefer-package-import only reports an implementation import "
            "when an ancestor initializer already re-exports every imported name. "
            "Where the facade is empty or partial, an enabled IMP004 passes without "
            "checking anything. On full checks, this hint counts distinct outside "
            "production modules whose effective IMP004 setting is explicit and that "
            "import public names from an implementation module that no eligible "
            "facade can supply. The evidence goes to the nearest enclosing package "
            "with an initializer outside the caller; package-internal, test, "
            "initializer, private, star and facade imports do not count, and "
            "IMP004 ignore entries exclude their callers. It reports the package once "
            "its callers exceed max (default 2). A facade with its own active "
            "architecture/missing-facade-export threshold is left to that rule. "
            "Changed, staged and path-scoped checks skip this hint."
        ),
        bad=(
            "# app/jobs/__init__.py is empty\n"
            "# app/api.py, app/cli.py and app/web.py each contain:\n"
            "from app.jobs.worker import start, stop"
        ),
        good=(
            "# app/jobs/__init__.py\nfrom .worker import start, stop\n\n"
            '__all__ = ["start", "stop"]'
        ),
        fix=(
            "Review whether to re-export these names. Re-exports can change "
            "import-time behavior. For an intentionally facade-less package, add an "
            "ignore entry for MET005 matching the package with a reason."
        ),
        canonical="Re-export from package facades the names other modules import",
        config=("rules", "ignore"),
    )


def unused_suppression_fix(
    text: str, suppression: Suppression, *, unused: tuple[str, ...]
) -> Fix | None:
    """Drop the unused identifiers, or the whole directive when `unused` is empty.

    Safe: the removed identifiers suppress nothing in a run that evaluated them.
    """
    drop = {_key(suppression.label(code)): () for code in unused}
    edit = _directive_edit(text, suppression, drop=drop or None)
    if edit is None:
        return None
    return Fix(
        Applicability.SAFE,
        "Remove the unused suppression"
        if not unused
        else "Remove the unused identifiers from the suppression",
        (edit,),
    )


def unknown_identifier_fix(
    text: str, suppression: Suppression, *, unknown: list[str]
) -> Fix | None:
    """Replace retired identifiers with their successors and drop the others.

    Replacing a retired name is safe. Dropping an identifier nothing knows is only
    suggested: it is often a typo of the rule the comment means to suppress.
    """
    drop = {_key(label): replacement_selectors(label) for label in unknown}
    edit = _directive_edit(text, suppression, drop=drop)
    if edit is None:
        return None
    if all(drop.values()):
        return Fix(
            Applicability.SAFE,
            "Replace retired identifiers with their current names",
            (edit,),
        )
    return Fix(
        Applicability.DISPLAY,
        "Remove unknown identifiers that match no rule",
        (edit,),
    )


def _directive_edit(
    text: str, suppression: Suppression, *, drop: Mapping[str, tuple[str, ...]] | None
) -> TextEdit | None:
    """Rewrite the directive's identifiers through `drop`, or remove it entirely.

    `drop` maps identifiers to their replacements, () removing them; None, or
    nothing left, removes the directive and whitespace before it, or its whole
    line when nothing else is on it.
    """
    source = SourceText(text)
    start, end = source.line_start(suppression.line), source.line_end(suppression.line)
    line = text[start:end].rstrip("\r\n")
    match = DIRECTIVE.match(line, suppression.column)
    if match is None:
        return None
    if drop is not None and match.group("codes") is not None:
        kept = [
            spelling
            for item in match.group("codes").split(",")
            if item.strip()
            for spelling in drop.get(_key(item.strip()), (item.strip(),))
        ]
        if kept:
            span = (start + match.start("codes"), start + match.end("codes"))
            return TextEdit.at(text, span, ", ".join(kept))
    cut = len(line[: match.start()].rstrip(" \t"))
    if not line[:cut].strip() and not line[match.end() :].strip():
        return TextEdit.at(text, (start, end), "")
    return TextEdit.at(text, (start + cut, start + match.end()), "")


def _key(identifier: str) -> str:
    """Rule names keep their spelling; codes and code prefixes ignore case."""
    return identifier if is_rule_name(identifier) else identifier.upper()
