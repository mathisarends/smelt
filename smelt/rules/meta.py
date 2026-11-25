from smelt.analysis.context import Index
from smelt.diagnostics.fixes import (
    FixAvailability,
)
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class _EngineRule(Rule):
    """Meta rules are judged after the other findings are filtered."""

    category = Category.META
    scope = "project"
    enabled_by_default = True


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


class MissingToolSuppressionReason(Rule):
    code = "MET006"
    name = "meta/missing-tool-suppression-reason"
    category = Category.META
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A noqa, type: ignore or other tool suppression without a reason.",
        rationale=(
            "Silencing a linter or type checker is the quickest way to a passing "
            "result, and the directive records only that a finding was hidden, not "
            "why. Without the reason a reviewer cannot tell a known stub defect from "
            "a real bug, or decide when the suppression can go. This opt-in rule "
            "reports `noqa`, `ruff: noqa`, `flake8: noqa`, `type: ignore`, "
            "`pyright: ignore`, `mypy: ignore-errors`, `mypy: disable-error-code` "
            "and `pylint: disable`/`disable-next` comments in production and test "
            "modules, with or without codes. A file-level directive is one finding "
            "on its line. Text after the directive counts as the reason, for "
            "example ` -- stub is wrong, see #123`, as does a comment block directly "
            "above the line that says more than further directives. A reason above "
            "a multi-line statement only counts when it ends right above the "
            "directive's line. `pragma: no cover`, `fmt:` and smelt's own "
            "suppressions are out of scope; meta/missing-suppression-reason covers "
            "the latter. Whether the suppression is still needed remains each "
            "tool's job (`RUF100`, `--warn-unused-ignores`). Files excluded in "
            "`project.exclude`, such as generated code, are not checked."
        ),
        bad="result = client.fetch()  # type: ignore[no-any-return]",
        good=(
            "result = client.fetch()  "
            "# type: ignore[no-any-return] -- the SDK stub returns Any, see #123"
        ),
        fix=(
            "Fix the reported problem so the directive can go. When the suppression "
            "must stay, append ` -- <reason>` to the comment or put the reason on "
            "the comment line above. No source is rewritten. Enable rules: "
            "{meta/missing-tool-suppression-reason: warning}, or select MET006."
        ),
        canonical="Give every noqa, type: ignore and pylint: disable comment a -- reason",
        config=("rules", "project.exclude"),
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
