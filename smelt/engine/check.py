from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext
from smelt.analysis.coverage import CoverageIndex
from smelt.analysis.errors import (
    InvalidOptionError as InvalidOptionError,  # noqa: PLC0414 - preserve the public engine exception import
)
from smelt.analysis.files import FileIndex
from smelt.config.errors import ConfigError, ConfigIssue, did_you_mean
from smelt.config.workspace import debt_scope_for_path, debt_scopes
from smelt.diagnostics.debt import Debt, DebtEntry
from smelt.diagnostics.fixes import FixAvailability
from smelt.diagnostics.report import ConfigurationDiagnostic, Report, RuleMeta
from smelt.diagnostics.violation import (
    Category,
    Severity,
    Violation,
    docs_url,
)
from smelt.engine import native_core
from smelt.engine.changes import Comparison, resolve_comparison
from smelt.engine.paths import missing_paths, project_metadata
from smelt.engine.rule_settings import RuleSettings
from smelt.rules.meta import (
    ResolvedDebt,
)
from smelt.rules.registry import build_rule_set
from smelt.rules.renames import retired_hint

if TYPE_CHECKING:
    from collections.abc import Callable

    from smelt.config.loader import LoadedConfig
    from smelt.config.source import SourceStore
    from smelt.rules.activation import PolicyActivation
    from smelt.rules.base import RuleContract, RuleSet

# The test-quality rules, which `smelt evaluate` selects by default.
TEST_QUALITY_CODES = (
    "TST002",
    "TST003",
    "TST004",
    "TST005",
    "TST006",
    "TST007",
    "TST008",
    "TST009",
    "TST010",
)
_COVERAGE = "ARC013"


_CATEGORY_ORDER = [category for category in Category if category is not Category.META]


@dataclass(frozen=True, slots=True)
class CheckOptions:
    paths: tuple[str, ...] = ()  # project-relative POSIX paths
    changed: bool = False
    base: str | None = None
    select: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    fail_on: Severity = Severity.ERROR
    use_debt: bool = True
    strict_coverage: bool = False
    show_coverage: bool = False
    comparison_root: Path | None = None
    staged: bool = False
    repository_root: Path | None = None
    comparison: Comparison | None = None


@dataclass(slots=True)
class CheckOutcome:
    report: Report
    context: AnalysisContext
    rules: RuleSet
    active: list[tuple[RuleContract, Severity]]
    # every violation after ignores and suppressions, before debt and scoping
    unfiltered: list[Violation] = field(default_factory=list)
    resolved_debt: list[DebtEntry] = field(default_factory=list)


def rule_meta(rule: RuleContract) -> RuleMeta:
    default = bool(getattr(rule, "enabled_by_default", False))
    activation = getattr(rule, "activation", None)
    if default:
        description = "Enabled by default."
    elif activation:
        description = _policy_description(activation)
    else:
        description = "Enable by rule setting or --select."
    return RuleMeta(
        code=rule.code,
        name=rule.name,
        category=rule.category,
        default_severity=rule.default_severity,
        enabled_by_default=default,
        summary=rule.explain().summary,
        docs_url=docs_url(rule.code),
        requires=tuple(sorted(index.value for index in rule.requires)),
        config=rule.explain().config,
        activation={
            "mode": "default" if default else "explicit",
            "description": description,
        },
        scope=getattr(rule, "scope", "source"),
        fix=getattr(rule, "fix_availability", FixAvailability.NONE),
    )


def run_check(
    loaded: LoadedConfig,
    options: CheckOptions,
    *,
    context: AnalysisContext | None = None,
) -> CheckOutcome:
    try:
        return _run_check(loaded, options, context=context)
    except ConfigError as exc:
        explained = loaded.explain_error(exc)
        if explained is exc:
            raise
        raise explained from exc


def snippet_reader(ctx: AnalysisContext) -> Callable[[Violation], str]:
    # Fingerprints need source lines, not source discovery. A lightweight
    # FileIndex decodes them as the analysis does.
    files = ctx.__dict__.get("files") or FileIndex(
        ctx.root, ctx.config, store=ctx.store
    )

    def snippet(violation: Violation) -> str:
        if not violation.path or not violation.line:
            return ""
        try:
            return files.line(violation.path, violation.line)
        except OSError:
            return ""

    return snippet


def relative_scope(
    root: Path, cwd: Path, raw: str, *, store: SourceStore | None = None
) -> str:
    absolute = store.resolve(cwd / raw) if store else (cwd / raw).resolve()
    try:
        return absolute.relative_to(
            store.resolve(root) if store else root.resolve()
        ).as_posix()
    except ValueError:
        path = Path(raw)
        return path.as_posix()


def _run_check(
    loaded: LoadedConfig,
    options: CheckOptions,
    *,
    context: AnalysisContext | None,
) -> CheckOutcome:
    config = loaded.config
    if options.strict_coverage and not config.strict_coverage:
        config = config.with_strict_coverage()
        loaded = replace(loaded, config=config)
    # Resolve built-in and project rule selectors against the same snapshot.
    rules = build_rule_set(loaded, lazy=True)
    active = _resolve_active_rules(rules, loaded, options)
    ctx = _analysis_context(loaded, context)
    compared = _comparison(ctx, options) if options.changed else None
    result = _native_analysis(rules, loaded, options, compared=compared)
    analyzed, baseline = result.analysis, result.baseline
    if "files" not in ctx.__dict__:
        ctx.files = FileIndex.from_inventory(
            ctx.root, ctx.config, result.files, store=ctx.store
        )
    if "coverage" not in ctx.__dict__:
        ctx.coverage = CoverageIndex.from_report(analyzed.coverage)
    _validate_ownership(result.ownership_issues, loaded)
    if issues := missing_paths(ctx):
        raise ConfigError(issues, loaded.path.name)
    settings = RuleSettings(rules, loaded)
    settings.validate_packages(ctx)
    if options.paths:
        _validate_scope(ctx, options.paths)
    # A fix applies only to the text the core judged.
    ctx.files.observe(result.digests)
    violations, suppressed = analyzed.violations, analyzed.suppressed
    unfiltered = list(violations)
    coverage_gaps = [v for v in violations if v.code == _COVERAGE]
    violations = [v for v in violations if v.code != _COVERAGE]

    debt = _match_debt(
        ctx, RuleSettings(rules, loaded), violations, active=active, options=options
    )
    violations = debt.violations
    comparison = compared[1] if compared else None
    if baseline is not None:
        _read_quoted_sources(ctx, violations)
        violations = baseline.match(violations, snippet_reader(ctx))[0]

    violations = coverage_gaps + [v for v in violations if _in_scope(v, options)]
    violations.sort(key=Violation.sort_key)
    categories = {rule.category for rule, _ in active}
    report = Report(
        violations=violations,
        modules=analyzed.modules,
        categories=[c for c in _CATEGORY_ORDER if c in categories],
        rules=[rule_meta(rule) for rule, _ in active],
        fail_on=options.fail_on,
        suppressed=suppressed,
        in_debt=debt.in_debt,
        scope=options.paths,
        checked_files=sum(
            _under_any(path, options.paths) if options.paths else True
            for path in analyzed.paths
        ),
        comparison=comparison.to_json() if comparison else None,
        coverage={
            k: v
            for k, v in analyzed.coverage.items()
            if k != "inventory" or options.show_coverage
        },
        project=project_metadata(loaded),
        configuration_diagnostics=[
            ConfigurationDiagnostic(
                issue.source or loaded.path.name,
                issue.path,
                issue.message,
                Severity.WARNING,
            )
            for issue in (
                *loaded.warnings,
                *RuleSettings(rules, loaded).unused_policies(),
            )
        ],
    )
    return CheckOutcome(
        report,
        ctx,
        rules,
        active,
        unfiltered,
        debt.resolved,
    )


@dataclass(frozen=True, slots=True)
class _DebtMatch:
    """The findings no debt entry records, with what the debt files matched."""

    violations: list[Violation]
    in_debt: int
    resolved: list[DebtEntry]


def _match_debt(
    ctx: AnalysisContext,
    policy: RuleSettings,
    violations: list[Violation],
    *,
    active: list[tuple[RuleContract, Severity]],
    options: CheckOptions,
) -> _DebtMatch:
    in_debt = 0
    resolved: list[DebtEntry] = []
    severity_of = {rule.code: severity for rule, severity in active}
    scopes = debt_scopes(policy.config) if options.use_debt else ()
    if scopes:
        _read_quoted_sources(ctx, violations)
    for debt_scope in scopes:
        debt = Debt.load(ctx.root / debt_scope.path, store=ctx.store)
        # Inactive rules have not been checked; their debt is not resolved.
        debt = Debt(
            [
                entry
                for entry in debt.entries
                if debt_scope_for_path(scopes, entry.path) == debt_scope
                and entry.code in severity_of
                and (rule := policy.rules.by_code(entry.code)) is not None
                and policy.severity_for_path(
                    rule, ctx, entry.path, selected=bool(options.select)
                )
                is not None
            ]
        )
        violations, known, scope_resolved = debt.match(violations, snippet_reader(ctx))
        resolved.extend(scope_resolved)
        in_debt += len(known)
        if "MET003" in severity_of and not options.paths and not options.changed:
            violations.extend(
                replace(finding, member=debt_scope.member)
                for finding in _resolved_violations(
                    scope_resolved, debt_scope.path, severity_of["MET003"]
                )
            )
    return _DebtMatch(violations, in_debt, resolved)


def _read_quoted_sources(ctx: AnalysisContext, violations: list[Violation]) -> None:
    """Read the files whose lines debt fingerprints quote in one Git request."""
    ctx.store.prefetch(
        ctx.root / violation.path
        for violation in violations
        if violation.path and violation.line
    )


def _native_analysis(
    rules: RuleSet,
    loaded: LoadedConfig,
    options: CheckOptions,
    *,
    compared: tuple[Path, Comparison] | None,
) -> native_core.NativeAnalysis:
    revision = compared[1].revision if compared else None
    run = native_core.NativeRun(
        select=_codes(rules, options.select),
        ignore=_codes(rules, options.ignore),
        strict_coverage=options.strict_coverage,
        configuration_findings=not (options.paths or options.changed or options.staged),
        baseline=(
            native_core.Baseline(compared[0], revision)
            if compared and revision
            else None
        ),
    )
    return native_core.analyze(loaded, run)


def _codes(rules: RuleSet, prefixes: tuple[str, ...]) -> tuple[str, ...]:
    """The rule codes that prefixes already validated by `_resolve_active_rules` name."""
    return tuple(code for prefix in prefixes for code in rules.matching_codes(prefix))


def _analysis_context(
    loaded: LoadedConfig, context: AnalysisContext | None
) -> AnalysisContext:
    if context is not None and context.config == loaded.config:
        return context
    return AnalysisContext(loaded.root, loaded.config, store=loaded.source)


def _validate_ownership(issues: list[list[str]], loaded: LoadedConfig) -> None:
    """Reject the `architecture.canonical_exports` the core could not verify."""
    if issues:
        raise ConfigError(
            [
                ConfigIssue(f"architecture.canonical_exports.{symbol}", message)
                for symbol, message in issues
            ],
            loaded.path.name,
        )


def _comparison(ctx: AnalysisContext, options: CheckOptions) -> tuple[Path, Comparison]:
    """The repository and the commit a `--changed` check compares with."""
    if options.comparison is not None and options.repository_root is not None:
        repository, comparison = options.repository_root, options.comparison
    else:
        repository, comparison = resolve_comparison(
            options.comparison_root or ctx.root,
            options.base,
            repository=options.repository_root,
        )
    if options.staged:
        comparison = replace(comparison, staged=True)
    return repository, comparison


def _resolve_active_rules(
    rules: RuleSet, loaded: LoadedConfig, options: CheckOptions
) -> list[tuple[RuleContract, Severity]]:
    policy = RuleSettings(rules, loaded)
    known = {rule.code for rule in rules.rules}
    normalized: dict[str, tuple[str, ...]] = {}
    for key in ("select", "ignore"):
        selectors: list[str] = []
        for prefix in getattr(options, key):
            matches = rules.matching_codes(prefix)
            if not matches:
                msg = (
                    f'unknown rule prefix "{prefix}" for --{key}{retired_hint(prefix, {}) or _prefix_suggestion(prefix, known)}; '
                    "`smelt rules` lists valid codes"
                )
                raise InvalidOptionError(msg, option=f"--{key}", value=prefix)
            selectors.extend(matches)
        normalized[key] = tuple(selectors)
    options = replace(options, select=normalized["select"], ignore=normalized["ignore"])

    active: list[tuple[RuleContract, Severity]] = []
    for rule in rules.rules:
        setting = policy.global_value(rule)
        if rule.code == _COVERAGE and loaded.config.strict_coverage:
            _validate_strict_coverage(loaded, options, setting)
            active.append((rule, Severity.ERROR))
            continue
        explicitly_selected = rule.code in options.select
        package_enabled = policy.has_package_activation(rule.code)
        if setting == "off" and not package_enabled:
            continue
        if setting is not None and setting != "off":
            severity = Severity(setting)
        elif explicitly_selected or package_enabled:
            severity = rule.default_severity
        else:
            continue
        if options.select and not explicitly_selected:
            continue
        if rule.code in options.ignore:
            continue
        active.append((rule, severity))
    return active


def _validate_strict_coverage(
    loaded: LoadedConfig, options: CheckOptions, setting: str | None
) -> None:
    if setting is not None and setting != "error":
        raise ConfigError(
            [
                ConfigIssue(
                    f"rules.{_COVERAGE}",
                    "strict coverage must remain an error; use reasoned project.exclude entries instead",
                )
            ],
            loaded.path.name,
        )
    if _COVERAGE in options.ignore:
        raise InvalidOptionError(
            "--ignore cannot disable strict coverage; use reasoned project.exclude entries instead",
            option="--ignore",
            value=",".join(options.ignore),
        )


def _prefix_suggestion(raw: str, known: set[str]) -> str:
    wanted = raw.strip().upper()
    candidates = {code[: len(wanted)] for code in known if len(code) >= len(wanted)}
    close = [
        code
        for code in candidates
        if sum(a != b for a, b in zip(code, wanted, strict=True)) == 1
    ]
    return f' (did you mean "{close[0]}"?)' if len(close) == 1 else ""


def _resolved_violations(
    resolved: list[DebtEntry], debt_path: str, severity: Severity
) -> list[Violation]:
    rule = ResolvedDebt()
    return [
        rule.violation(
            f"resolved debt entry: {entry.code} {entry.path or ''} ({entry.message})",
            path=debt_path,
            hint="Run `smelt debt --prune` to remove resolved entries.",
        ).with_severity(severity)
        for entry in resolved
    ]


def _in_scope(violation: Violation, options: CheckOptions) -> bool:
    return not options.paths or _under_any(violation.path, options.paths)


def _validate_scope(ctx: AnalysisContext, paths: tuple[str, ...]) -> None:
    known = list(ctx.files.all_check_paths())
    candidates = [*known, *(p.path for p in ctx.files.packages.values())]
    for path in paths:
        if not ctx.store.exists(ctx.root / path):
            msg = f'check path "{path}" does not exist{did_you_mean(path, candidates)}'
            raise InvalidOptionError(msg, option="paths", value=path)
        if not any(_under_any(p, (path,)) for p in known):
            msg = (
                f'check path "{path}" contains no analyzed Python source or test files or configured YAML files; '
                "check project.root_packages, source_roots, test_roots and exclude"
            )
            raise InvalidOptionError(msg, option="paths", value=path)


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


def _policy_description(activation: PolicyActivation) -> str:
    policy = activation.description[0].lower() + activation.description[1:]
    if activation.required:
        return f"Enable by rule setting or --select; requires that {policy}"
    return f"Enable by rule setting or --select; configurable when {policy}"
