from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.config.errors import ConfigError, ConfigIssue, did_you_mean
from smelt.config.patterns import module_matches, path_matches
from smelt.diagnostics.baseline import Baseline, BaselineEntry
from smelt.diagnostics.dedupe import deduplicate
from smelt.diagnostics.report import Report, RuleMeta
from smelt.diagnostics.suppressions import (
    Suppression,
    parse_suppressions,
    without_codes,
)
from smelt.diagnostics.violation import (
    Category,
    Fix,
    LineEdit,
    Severity,
    Violation,
    docs_url,
)
from smelt.engine.changes import git_changes
from smelt.rules.meta import StaleBaseline, SuppressionWithoutReason, UnusedSuppression
from smelt.rules.registry import build_rule_set

if TYPE_CHECKING:
    from collections.abc import Callable

    from smelt.config.loader import LoadedConfig
    from smelt.rules.base import Rule, RuleSet

_META = frozenset({"SMT901", "SMT902", "SMT903"})
_CATEGORY_ORDER = [
    Category.DEPENDENCIES,
    Category.CODE,
    Category.STRUCTURE,
    Category.TESTS,
]


@dataclass(frozen=True)
class CheckOptions:
    paths: tuple[str, ...] = ()  # project-relative POSIX paths
    changed: bool = False
    base: str | None = None
    select: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    fail_on: Severity = Severity.ERROR
    use_baseline: bool = True


@dataclass
class CheckOutcome:
    report: Report
    context: AnalysisContext
    rules: RuleSet
    active: list[tuple[Rule, Severity]]
    # every violation after ignores and suppressions, before baseline and scoping
    unfiltered: list[Violation] = field(default_factory=list)
    stale_baseline: list[BaselineEntry] = field(default_factory=list)


def load_rules(loaded: LoadedConfig) -> RuleSet:
    config = loaded.config
    search = [loaded.root, *(loaded.root / r for r in config.project.source_roots)]
    return build_rule_set(config.plugins, search_paths=search)


def rule_meta(rule: Rule) -> RuleMeta:
    return RuleMeta(
        code=rule.code,
        name=rule.name,
        category=rule.category,
        default_severity=rule.default_severity,
        enabled_by_default=bool(getattr(rule, "enabled_by_default", True)),
        fixable=rule.fixable,
        summary=rule.explain().summary,
        docs_url=docs_url(rule.code),
    )


def resolve_active_rules(
    rules: RuleSet, loaded: LoadedConfig, options: CheckOptions
) -> list[tuple[Rule, Severity]]:
    config = loaded.config
    known = {rule.code for rule in rules.rules}
    issues = [
        ConfigIssue(
            f"rules.{code}", f'unknown rule "{code}"{did_you_mean(code, known)}'
        )
        for code in config.rules
        if code not in known
    ]
    if issues:
        raise ConfigError(issues, loaded.path.name)

    active: list[tuple[Rule, Severity]] = []
    for rule in rules.rules:
        setting = config.rules.get(rule.code)
        if setting is None and rule.code == "SMT406":
            setting = config.tests.interaction_assertions
        explicitly_selected = rule.code in options.select
        if setting == "off":
            continue
        if setting is not None:
            severity = Severity(setting)
        elif getattr(rule, "enabled_by_default", True) or explicitly_selected:
            severity = rule.default_severity
        else:
            continue
        if options.select and not _matches_prefix(rule.code, options.select):
            continue
        if options.ignore and _matches_prefix(rule.code, options.ignore):
            continue
        if Index.CHANGES in rule.requires and not options.changed:
            continue
        active.append((rule, severity))
    return active


def _matches_prefix(code: str, prefixes: tuple[str, ...]) -> bool:
    return any(code.startswith(prefix.strip().upper()) for prefix in prefixes if prefix)


def run_check(
    loaded: LoadedConfig,
    options: CheckOptions,
    *,
    rules: RuleSet | None = None,
    context: AnalysisContext | None = None,
) -> CheckOutcome:
    config = loaded.config
    rules = rules or load_rules(loaded)
    active = resolve_active_rules(rules, loaded, options)
    ctx = context or AnalysisContext(loaded.root, config)
    if options.changed and ctx.changes is None:
        ctx.changes = git_changes(loaded.root, options.base)
    requires = frozenset(index for rule, _ in active for index in rule.requires)
    ctx.ensure(requires)

    violations = [
        violation.with_severity(severity)
        for rule, severity in active
        if rule.code not in _META
        for violation in rule.check(ctx)
    ]
    violations = deduplicate(violations)
    violations = _apply_ignores(ctx, violations)

    active_codes = {rule.code for rule, _ in active}
    severity_of = {rule.code: severity for rule, severity in active}
    suppressions = _collect_suppressions(ctx)
    violations, suppressed = _apply_suppressions(violations, suppressions)
    known_codes = {rule.code for rule in rules.rules}
    violations.extend(
        _suppression_violations(
            ctx, suppressions, active_codes, known_codes, severity_of
        )
    )
    unfiltered = list(violations)

    baselined = 0
    stale: list[BaselineEntry] = []
    if options.use_baseline and config.baseline:
        baseline = Baseline.load(loaded.root / config.baseline)
        violations, known, stale = baseline.match(violations, snippet_reader(ctx))
        baselined = len(known)
        full_run = not options.paths and not options.changed
        if "SMT903" in active_codes and full_run:
            violations.extend(
                _stale_violations(stale, config.baseline, severity_of["SMT903"])
            )

    violations = [v for v in violations if _in_scope(ctx, v, options)]
    violations.sort(key=Violation.sort_key)
    categories = {rule.category for rule, _ in active}
    report = Report(
        violations=violations,
        modules=len(ctx.files.sources),
        categories=[c for c in _CATEGORY_ORDER if c in categories],
        rules=[rule_meta(rule) for rule, _ in active],
        fail_on=options.fail_on,
        suppressed=suppressed,
        baselined=baselined,
    )
    return CheckOutcome(report, ctx, rules, active, unfiltered, stale)


def snippet_reader(ctx: AnalysisContext) -> Callable[[Violation], str]:
    def snippet(violation: Violation) -> str:
        if not violation.path or not violation.line:
            return ""
        try:
            return ctx.files.line(violation.path, violation.line)
        except OSError:
            return ""

    return snippet


def _apply_ignores(
    ctx: AnalysisContext, violations: list[Violation]
) -> list[Violation]:
    entries = ctx.config.ignore
    if not entries:
        return violations
    kept: list[Violation] = []
    for violation in violations:
        module = violation.source_module or _module_for_path(ctx, violation.path)
        ignored = any(
            violation.code.startswith(entry.rule)
            and (
                (module and any(module_matches(p, module) for p in entry.modules))
                or (
                    violation.path
                    and any(path_matches(p, violation.path) for p in entry.paths)
                )
            )
            for entry in entries
        )
        if not ignored:
            kept.append(violation)
    return kept


def _module_for_path(ctx: AnalysisContext, path: str | None) -> str | None:
    if path is None:
        return None
    source = ctx.files.source_for_path(path)
    return source.module if source else None


def _collect_suppressions(ctx: AnalysisContext) -> list[Suppression]:
    found: list[Suppression] = []
    for path in ctx.files.all_python_paths():
        text = ctx.files.read_text(path)
        if "smelt:" in text:
            found.extend(parse_suppressions(path, text))
    return found


def _apply_suppressions(
    violations: list[Violation], suppressions: list[Suppression]
) -> tuple[list[Violation], int]:
    by_path: dict[str, list[Suppression]] = {}
    for suppression in suppressions:
        by_path.setdefault(suppression.path, []).append(suppression)
    kept: list[Violation] = []
    suppressed = 0
    for violation in violations:
        match = next(
            (s for s in by_path.get(violation.path or "", []) if s.matches(violation)),
            None,
        )
        if match is None:
            kept.append(violation)
        else:
            match.mark_used(violation.code)
            suppressed += 1
    return kept, suppressed


def _suppression_violations(
    ctx: AnalysisContext,
    suppressions: list[Suppression],
    active_codes: set[str],
    known_codes: set[str],
    severity_of: dict[str, Severity],
) -> list[Violation]:
    results: list[Violation] = []
    unused_rule = UnusedSuppression()
    reason_rule = SuppressionWithoutReason()
    checked = active_codes - _META
    for suppression in suppressions:
        if (
            "SMT902" in active_codes
            and ctx.config.suppressions.require_reason
            and not suppression.reason
        ):
            results.append(
                reason_rule.violation(
                    "suppression without a reason",
                    path=suppression.path,
                    line=suppression.line,
                    column=suppression.column + 1,
                    hint="Append ` -- <why this exception is acceptable>` to the comment.",
                ).with_severity(severity_of["SMT902"])
            )
        if "SMT901" not in active_codes:
            continue
        unused = _unused_codes(suppression, checked, known_codes)
        if unused is None:
            continue
        line = ctx.files.line(suppression.path, suppression.line)
        # An empty tuple means the whole comment goes, so name every code it carries.
        replacement = without_codes(line, suppression, unused or suppression.codes)
        label = ", ".join(unused) if unused else "all codes"
        results.append(
            unused_rule.violation(
                f"unused suppression ({label})",
                path=suppression.path,
                line=suppression.line,
                column=suppression.column + 1,
                hint="Remove the suppression; nothing on this line needs it.",
                fix=Fix(
                    "remove unused suppression",
                    (LineEdit(suppression.path, suppression.line, replacement),),
                ),
            ).with_severity(severity_of["SMT901"])
        )
    return results


def _unused_codes(
    suppression: Suppression, checked: set[str], known: set[str]
) -> tuple[str, ...] | None:
    """Codes to remove, ``()`` for the whole comment, None if the suppression is fine.

    A code only counts as unused when every rule it refers to actually ran.
    """
    if not suppression.codes:
        return None if suppression.is_used or checked != known - _META else ()
    evaluated = tuple(
        prefix
        for prefix in suppression.unused_codes()
        if (matching := {code for code in known if code.startswith(prefix)})
        and matching <= checked
    )
    if not evaluated:
        return None
    if len(evaluated) == len(suppression.codes):
        return ()
    return evaluated


def _stale_violations(
    stale: list[BaselineEntry], baseline_path: str, severity: Severity
) -> list[Violation]:
    rule = StaleBaseline()
    return [
        rule.violation(
            f"stale baseline entry: {entry.code} {entry.path or ''} ({entry.message})",
            path=baseline_path,
            hint="Run `smelt baseline --prune` to remove fixed entries.",
        ).with_severity(severity)
        for entry in stale
    ]


def _in_scope(
    ctx: AnalysisContext, violation: Violation, options: CheckOptions
) -> bool:
    if options.paths and not _under_any(violation.path, options.paths):
        return False
    if options.changed and ctx.changes is not None:
        if violation.path in ctx.changes:
            return True
        return any(
            (path := ctx.files.path_for_module(module)) is not None
            and path in ctx.changes
            for module in violation.involved_modules()
        )
    return True


def _under_any(path: str | None, scopes: tuple[str, ...]) -> bool:
    if path is None:
        return False
    for scope in scopes:
        normalized = scope.strip("/")
        if normalized in ("", "."):
            return True
        if path == normalized or path.startswith(f"{normalized}/"):
            return True
    return False


def relative_scope(root: Path, cwd: Path, raw: str) -> str:
    absolute = (cwd / raw).resolve()
    try:
        return absolute.relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(raw).as_posix()
