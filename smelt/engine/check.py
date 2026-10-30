from __future__ import annotations

import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from smelt.analysis.cache import FactCache
from smelt.analysis.context import AnalysisContext
from smelt.analysis.files import FileIndex
from smelt.analysis.parsing import AnalysisError
from smelt.config.errors import ConfigError, ConfigIssue, did_you_mean
from smelt.config.patterns import module_matches, path_matches
from smelt.diagnostics.debt import Debt, DebtEntry
from smelt.diagnostics.dedupe import deduplicate
from smelt.diagnostics.report import ConfigurationDiagnostic, Report, RuleMeta
from smelt.diagnostics.suppressions import (
    Suppression,
    parse_suppressions,
)
from smelt.diagnostics.violation import (
    Category,
    Severity,
    Violation,
    docs_url,
)
from smelt.engine.cache import ANALYSIS, CachedAnalysis, analysis_inputs
from smelt.engine.changes import Comparison, export_base, resolve_comparison
from smelt.engine.coverage import effective_coverage
from smelt.engine.paths import missing_paths, project_metadata
from smelt.engine.rule_settings import RuleSettings
from smelt.rules.meta import ResolvedDebt, SuppressionWithoutReason, UnusedSuppression
from smelt.rules.registry import build_rule_set

if TYPE_CHECKING:
    from collections.abc import Callable

    from smelt.config.loader import LoadedConfig
    from smelt.rules.base import Rule, RuleSet

_META = frozenset({"SMT901", "SMT902", "SMT903"})
_COVERAGE = "SMT306"
_BASELINE = TypeAdapter(list[DebtEntry])


class InvalidOptionError(AnalysisError):
    """A check option names something that does not exist; a usage error."""

    def __init__(self, message: str, *, option: str, value: str) -> None:
        super().__init__(message)
        self.option = option
        self.value = value


_CATEGORY_ORDER = [
    Category.DEPENDENCIES,
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
    use_debt: bool = True
    strict_coverage: bool = False
    show_coverage: bool = False
    use_cache: bool = True
    cache_dir: Path | None = None
    comparison_root: Path | None = None
    staged: bool = False
    snapshot_id: str | None = None
    repository_root: Path | None = None


@dataclass
class CheckOutcome:
    report: Report
    context: AnalysisContext
    rules: RuleSet
    active: list[tuple[Rule, Severity]]
    # every violation after ignores and suppressions, before debt and scoping
    unfiltered: list[Violation] = field(default_factory=list)
    resolved_debt: list[DebtEntry] = field(default_factory=list)


def rule_meta(rule: Rule) -> RuleMeta:
    default = bool(getattr(rule, "enabled_by_default", False))
    activation = getattr(rule, "activation", None)
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
            "mode": "default" if default else "policy" if activation else "explicit",
            "description": "Enabled by default."
            if default
            else activation.description
            if activation
            else "Enable by rule setting or --select.",
        },
        scope=getattr(rule, "scope", "source"),
    )


def _settings_by_code(rules: RuleSet, loaded: LoadedConfig) -> dict[str, str]:
    """Key ``rules:`` overrides by code; the config may name a rule by code or name."""
    return RuleSettings(rules, loaded).global_rules


def resolve_active_rules(
    rules: RuleSet, loaded: LoadedConfig, options: CheckOptions
) -> list[tuple[Rule, Severity]]:
    policy = RuleSettings(rules, loaded)
    settings = policy.global_rules
    known = {rule.code for rule in rules.rules}
    names = {rule.name: rule.code for rule in rules.rules}
    normalized: dict[str, tuple[str, ...]] = {}
    for key in ("select", "ignore"):
        selectors: list[str] = []
        for prefix in getattr(options, key):
            selector = names.get(prefix.strip().lower(), prefix.strip().upper())
            if selector != "ALL" and not any(
                code.startswith(selector) for code in known
            ):
                msg = (
                    f'unknown rule prefix "{prefix}" for --{key}{_prefix_suggestion(prefix, known)}; '
                    "`smelt rules` lists valid codes"
                )
                raise InvalidOptionError(msg, option=f"--{key}", value=prefix)
            selectors.append(selector)
        normalized[key] = tuple(selectors)
    options = replace(options, select=normalized["select"], ignore=normalized["ignore"])

    active: list[tuple[Rule, Severity]] = []
    for rule in rules.rules:
        setting = settings.get(rule.code)
        if rule.code == _COVERAGE and loaded.config.project.coverage == "strict":
            _validate_strict_coverage(loaded, options, setting)
            active.append((rule, Severity.ERROR))
            continue
        explicitly_selected = bool(options.select) and _matches_prefix(
            rule.code, options.select
        )
        package_enabled = policy.has_package_activation(rule.code)
        if setting == "off" and not package_enabled:
            continue
        if setting is not None and setting != "off":
            severity = Severity(setting)
        elif (
            policy.globally_enabled(rule, selected=explicitly_selected)
            or package_enabled
        ):
            severity = rule.default_severity
        else:
            continue
        if options.select and not _matches_prefix(rule.code, options.select):
            continue
        if options.ignore and _matches_prefix(rule.code, options.ignore):
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
    if _matches_prefix(_COVERAGE, options.ignore):
        raise InvalidOptionError(
            "--ignore cannot disable strict coverage; use reasoned project.exclude entries instead",
            option="--ignore",
            value=",".join(options.ignore),
        )


def _matches_prefix(code: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        prefix.strip().upper() == "ALL" or code.startswith(prefix.strip().upper())
        for prefix in prefixes
        if prefix
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


def run_check(
    loaded: LoadedConfig,
    options: CheckOptions,
    *,
    rules: RuleSet | None = None,
    context: AnalysisContext | None = None,
) -> CheckOutcome:
    cache = (
        FactCache(options.cache_dir or loaded.root / ".smelt_cache")
        if options.use_cache and rules is None and context is None
        else None
    )
    try:
        return _run_check(loaded, options, rules=rules, context=context, cache=cache)
    finally:
        if cache is not None:
            cache.close()


def _run_check(
    loaded: LoadedConfig,
    options: CheckOptions,
    *,
    rules: RuleSet | None,
    context: AnalysisContext | None,
    cache: FactCache | None,
) -> CheckOutcome:
    config = loaded.config
    if options.strict_coverage and config.project.coverage != "strict":
        config = config.model_copy(
            update={"project": config.project.model_copy(update={"coverage": "strict"})}
        )
        loaded = replace(loaded, config=config)
    rules = rules or build_rule_set()
    active = resolve_active_rules(rules, loaded, options)
    ctx = (
        context
        if context is not None and context.config == config
        else AnalysisContext(loaded.root, config, cache=cache)
    )
    key = (
        cache.key(
            "index-analysis",
            [
                options.snapshot_id,
                config.model_dump(mode="json"),
                [(r.code, s.value) for r, s in active],
                str(options.comparison_root),
                options.select,
            ],
        )
        if cache and options.snapshot_id
        else None
    )
    analyzed = cache.read(key, ANALYSIS) if cache and key else None
    if analyzed is None:
        issues = missing_paths(ctx)
        if issues:
            raise ConfigError(issues, loaded.path.name)
        RuleSettings(rules, loaded).validate_packages(ctx)
        key = key or (
            cache.key("analysis", analysis_inputs(ctx, active, options.select))
            if cache
            else None
        )
        analyzed = cache.read(key, ANALYSIS) if cache and key else None
    if options.paths:
        _validate_scope(ctx, options.paths)
    if analyzed is None:
        violations, suppressed = _collect(ctx, rules, loaded, active, options)
        analyzed = CachedAnalysis(
            violations,
            suppressed,
            effective_coverage(
                ctx,
                include_inventory=True,
                include_ordering=any(rule.code == "SMT313" for rule, _ in active),
                include_test_quality=any(
                    rule.code.startswith("SMT42") for rule, _ in active
                ),
            ),
            len(ctx.files.sources),
            list(ctx.files.all_python_paths()),
        )
        if cache and key:
            cache.write(key, analyzed, ANALYSIS)
    violations, suppressed = list(analyzed.violations), analyzed.suppressed
    unfiltered = list(violations)
    coverage_gaps = [v for v in violations if v.code == _COVERAGE]
    violations = [v for v in violations if v.code != _COVERAGE]

    in_debt = 0
    resolved: list[DebtEntry] = []
    severity_of = {rule.code: severity for rule, severity in active}
    if options.use_debt and config.debt:
        debt = Debt.load(loaded.root / config.debt)
        # Inactive rules have not been checked; their debt is not resolved.
        policy = RuleSettings(rules, loaded)
        debt = Debt(
            [
                entry
                for entry in debt.entries
                if entry.code in severity_of
                and (rule := rules.by_code(entry.code)) is not None
                and policy.severity_for_path(
                    rule, ctx, entry.path, selected=bool(options.select)
                )
                is not None
            ]
        )
        violations, known, resolved = debt.match(violations, snippet_reader(ctx))
        in_debt = len(known)
        full_run = not options.paths and not options.changed
        if "SMT903" in severity_of and full_run:
            violations.extend(
                _resolved_violations(resolved, config.debt, severity_of["SMT903"])
            )
    comparison: Comparison | None = None
    if options.changed:
        violations, comparison = _introduced(
            ctx,
            violations,
            lambda before: _collect(before, rules, loaded, active, options)[0],
            options=options,
            cache_key=[
                config.model_dump(mode="json"),
                [(r.code, s.value) for r, s in active],
                options.select,
            ],
        )

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
        in_debt=in_debt,
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
            ConfigurationDiagnostic(loaded.path.name, issue.path, issue.message)
            for issue in loaded.warnings
        ],
    )
    return CheckOutcome(report, ctx, rules, active, unfiltered, resolved)


def _introduced(
    ctx: AnalysisContext,
    violations: list[Violation],
    collect: Callable[[AnalysisContext], list[Violation]],
    *,
    options: CheckOptions,
    cache_key: object = None,
) -> tuple[list[Violation], Comparison]:
    """The violations the working tree adds to the base commit, judged by today's config."""
    root = options.comparison_root or ctx.root
    repository, comparison = resolve_comparison(
        root, options.base, repository=options.repository_root
    )
    if options.staged:
        comparison = replace(comparison, staged=True)
    cache = ctx.cache
    # A source root outside the repository can change independently of HEAD.
    if any(
        not (root / path).resolve().is_relative_to(repository.resolve())
        for path in [*ctx.config.project.source_roots, *ctx.config.project.test_roots]
    ):
        cache = None
    key = (
        cache.key(
            "baseline",
            [root.relative_to(repository).as_posix(), comparison.revision, cache_key],
        )
        if cache
        else None
    )
    entries = cache.read(key, _BASELINE) if cache and key else None
    if entries is not None:
        introduced, _, _ = Debt(entries).match(violations, snippet_reader(ctx))
        return introduced, comparison
    with tempfile.TemporaryDirectory(prefix="smelt-base-") as tmp:
        base_root = export_base(root, repository, comparison, Path(tmp))
        if base_root is None:
            return violations, comparison
        before_ctx = AnalysisContext(base_root, ctx.config, cache=ctx.cache)
        # A committed README (or a newly added workspace package) is an empty
        # historical inventory, not an analysis failure. Existing source still
        # goes through normal parsing so corrupt baselines do not pass silently.
        before = collect(before_ctx) if before_ctx.files.sources else []
        known = Debt.from_violations(before, snippet_reader(before_ctx))
        if cache and key:
            cache.write(key, known.entries, _BASELINE)
        introduced, _, _ = known.match(violations, snippet_reader(ctx))
    return introduced, comparison


def _collect(
    ctx: AnalysisContext,
    rules: RuleSet,
    loaded: LoadedConfig,
    active: list[tuple[Rule, Severity]],
    options: CheckOptions,
) -> tuple[list[Violation], int]:
    """Every violation after ignores and suppressions, and the suppressed count."""
    requires = frozenset(index for rule, _ in active for index in rule.requires)
    ctx.ensure(requires)

    policy = RuleSettings(rules, loaded)
    violations: list[Violation] = []
    for rule, _ in active:
        if rule.code in _META:
            continue
        for violation in rule.check(ctx):
            severity = policy.severity(
                rule, ctx, violation.source_module, selected=bool(options.select)
            )
            if severity is not None:
                violations.append(violation.with_severity(severity))
    violations = deduplicate(violations)
    violations = _apply_ignores(ctx, violations, rules)

    active_codes = {rule.code for rule, _ in active}
    severity_of = {rule.code: severity for rule, severity in active}
    suppressions = _collect_suppressions(ctx)
    violations, suppressed = _apply_suppressions(violations, suppressions)
    known_codes = {rule.code for rule in rules.rules}
    # The rules a full run with this config would execute; the others are off and
    # cannot need a suppression.
    enabled = resolve_active_rules(rules, loaded, CheckOptions())
    enabled_codes = {rule.code for rule, _ in enabled} - _META
    violations.extend(
        _suppression_violations(
            ctx, suppressions, active_codes, (known_codes, enabled_codes), severity_of
        )
    )
    return violations, suppressed


def snippet_reader(ctx: AnalysisContext) -> Callable[[Violation], str]:
    # A cached snapshot needs source lines, not source discovery, to compare
    # fingerprints. A lightweight FileIndex provides the same decoding/cache.
    files = ctx.__dict__.get("files") or FileIndex(ctx.root, ctx.config)

    def snippet(violation: Violation) -> str:
        if not violation.path or not violation.line:
            return ""
        try:
            return files.line(violation.path, violation.line)
        except OSError:
            return ""

    return snippet


def _apply_ignores(
    ctx: AnalysisContext, violations: list[Violation], rules: RuleSet
) -> list[Violation]:
    entries = ctx.config.ignore
    if not entries:
        return violations
    names = {rule.name: rule.code for rule in rules.rules}
    kept: list[Violation] = []
    for violation in violations:
        if violation.code == _COVERAGE:
            kept.append(violation)
            continue
        module = violation.source_module or _module_for_path(ctx, violation.path)
        ignored = any(
            (
                entry.rule == "ALL"
                or violation.code.startswith(names.get(entry.rule, entry.rule))
            )
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
        if violation.code == _COVERAGE:
            kept.append(violation)
            continue
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
    codes: tuple[set[str], set[str]],
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
        unused = _unused_codes(suppression, checked, *codes)
        if unused is None:
            continue
        label = ", ".join(unused) if unused else "all codes"
        results.append(
            unused_rule.violation(
                f"unused suppression ({label})",
                path=suppression.path,
                line=suppression.line,
                column=suppression.column + 1,
                hint="Remove the suppression; nothing on this line needs it.",
            ).with_severity(severity_of["SMT901"])
        )
    return results


def _unused_codes(
    suppression: Suppression, checked: set[str], known: set[str], enabled: set[str]
) -> tuple[str, ...] | None:
    """Codes to remove, ``()`` for the whole comment, None if the suppression is fine.

    A code only counts as unused when every enabled rule it refers to actually ran;
    a narrowed run (``--select``, ``--ignore``) cannot judge the others.
    """
    if not suppression.codes:
        return None if suppression.is_used or not enabled <= checked else ()
    evaluated = tuple(
        prefix
        for prefix in suppression.unused_codes()
        if (matching := {code for code in known if code.startswith(prefix)})
        and matching & enabled <= checked
    )
    if not evaluated:
        return None
    if len(evaluated) == len(suppression.codes):
        return ()
    return evaluated


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
    known = list(ctx.files.all_python_paths())
    candidates = [*known, *(p.path for p in ctx.files.packages.values())]
    for path in paths:
        if not (ctx.root / path).exists():
            msg = f'check path "{path}" does not exist{did_you_mean(path, candidates)}'
            raise InvalidOptionError(msg, option="paths", value=path)
        if not any(_under_any(p, (path,)) for p in known):
            msg = (
                f'check path "{path}" contains no analyzed Python source or test files; '
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


def relative_scope(root: Path, cwd: Path, raw: str) -> str:
    absolute = (cwd / raw).resolve()
    try:
        return absolute.relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(raw).as_posix()
