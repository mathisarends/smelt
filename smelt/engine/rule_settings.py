"""Resolve the same rule names for project and package settings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from smelt.config.errors import ConfigError, ConfigIssue, did_you_mean
from smelt.config.models import ALL_RULES, LIMIT_RULES, POLICY_DEFAULTS, RuleOverride
from smelt.config.patterns import module_matches
from smelt.config.workspace import architecture_scopes
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.renames import retired_hint

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from smelt.analysis.context import AnalysisContext
    from smelt.config.loader import LoadedConfig
    from smelt.config.models import RuleSetting
    from smelt.rules.base import RuleContract, RuleSet

_STRICT_COVERAGE = "ARC013"
_RANK = {"off": 0, "hint": 1, "warning": 2, "error": 3}
_LIMIT_CODES = dict(LIMIT_RULES.values())
# The comprehension limit counts for clauses, so a comprehension has at least one.
_MINIMUM_MAX = {"CPX006": 1}
# Project-wide limits: package and member entries cannot refine them.
_ROOT_LIMITS = frozenset(
    LIMIT_RULES[option][0]
    for option in ("max_patches_per_test", "max_uncovered_facade_callers")
)
_LIMIT_OPTIONS = {code: option for option, (code, _) in LIMIT_RULES.items()}


@dataclass(frozen=True, slots=True)
class _Setting:
    selector: str
    value: str
    broad: bool
    location: str
    reason: str | None
    source: str | None

    @property
    def enables(self) -> bool:
        return not self.broad and self.value != "off"

    def apply(self, inherited: str | None) -> str | None:
        if not self.broad or self.value == "off":
            return self.value
        if inherited is None:
            return None
        return min(inherited, self.value, key=_RANK.__getitem__)


type RuleState = Literal["on", "scoped", "off", "needs policy"]


@dataclass(frozen=True, slots=True)
class RuleStatus:
    """Whether a rule runs for the whole project, and at which severity."""

    state: RuleState
    severity: str | None = None


@dataclass(frozen=True, slots=True)
class RuleUsage:
    """How to enable a rule, which options configure it and what it requires."""

    enable: str
    options: tuple[str, ...]
    requires: str | None


def rule_usage(rule: RuleContract) -> RuleUsage:
    """The rules entry that enables ``rule``, its options and required policy."""
    severity = rule.default_severity.value
    options = []
    if (option := _LIMIT_OPTIONS.get(rule.code)) is not None:
        default = getattr(POLICY_DEFAULTS, option, None)
        options.append(
            f"max (default {default})" if default is not None else "max (required)"
        )
        enable = f"rules: {{{rule.name}: {{severity: {severity}, max: {default if default is not None else '<n>'}}}}}"
    else:
        enable = f"rules: {{{rule.name}: {severity}}}"
    if getattr(rule, "enabled_by_default", False):
        enable = f"on by default; turn it off with rules: {{{rule.name}: off}}"
    options.extend(key for key in rule.explain().config if key != "rules")
    activation = getattr(rule, "activation", None)
    requires = activation.description if activation and activation.required else None
    return RuleUsage(enable, tuple(options), requires)


def catalog_status(rule: RuleContract) -> RuleStatus:
    """The status of ``rule`` without a configuration."""
    if getattr(rule, "enabled_by_default", False):
        return RuleStatus("on", rule.default_severity.value)
    activation = getattr(rule, "activation", None)
    return RuleStatus(
        "needs policy" if activation is not None and activation.required else "off"
    )


@dataclass(frozen=True, slots=True)
class RuleSettingOrigin:
    location: str
    source: str | None


@dataclass(frozen=True, slots=True)
class RuleDecision:
    severity: Severity | None
    location: str | None = None
    reason: str | None = None
    source: str | None = None


class RuleSettings:
    def __init__(
        self, rules: RuleSet, loaded: LoadedConfig, *, validate: bool = True
    ) -> None:
        self.config = loaded.config
        # A member architecture's policy satisfies its rules like the root's does.
        self._policies = (
            self.config,
            *(scope.config for scope in architecture_scopes(self.config)[1:]),
        )
        self.source = loaded.path.name
        self.rules = rules
        self.codes = _rule_codes(rules)
        self._defaults = {
            rule.code: rule.default_severity.value for rule in rules.rules
        }
        project_rules = {
            rule.code
            for rule in rules.rules
            if getattr(rule, "scope", "source") != "source"
        }
        # A later layer replaces what an earlier one set: the level wins over specificity.
        self._layers: list[dict[str, _Setting]] = []
        self.selector_rules: dict[str, list[str]] = {}
        # Selector mistakes are reported together, so a migration needs one pass.
        pending: list[ConfigIssue] = []
        for index, layer in enumerate(loaded.config.rule_layers):
            source = (
                loaded.rule_sources[index] if index < len(loaded.rule_sources) else None
            )
            entries = self._entries(
                layer,
                "rules",
                excluded=self._strict_guard(),
                source=source,
                pending=pending,
            )
            self._layers.append(entries)
        self._packages = {
            name: self._entries(
                contract.rules,
                f"{contract.style_location or f'style.overrides.{name}'}.rules",
                excluded=frozenset(project_rules),
                pending=pending,
            )
            for name, contract in self.config.structure.contracts.items()
        }
        # Workspace members refine the root by path, so their tests are covered too.
        self._members = {
            path: self._entries(
                member.rules,
                f"members.{path}.rules",
                excluded=self._strict_guard(),
                pending=pending,
            )
            for path, member in loaded.config.members.items()
        }
        pending.extend(self._root_limit_issues(loaded))
        pending.extend(
            ConfigIssue(f"{location}.{key}.max", _ROOT_SCOPE)
            for location, settings in (
                *(
                    (f"style.overrides.{name}.rules", contract.rules)
                    for name, contract in self.config.style.overrides.items()
                ),
                *(
                    (f"members.{path}.rules", member.rules)
                    for path, member in loaded.config.members.items()
                ),
            )
            for key, value in settings.items()
            if self.codes.get(key) in _ROOT_LIMITS
            and isinstance(value, RuleOverride)
            and value.max is not None
        )
        if validate:
            self._validate_root_broad(loaded, pending)
        pending.extend(
            ConfigIssue(
                f"ignore[{index}].rule",
                f'unknown rule "{entry.rule}"{self._suggestion(entry.rule)}',
            )
            for index, entry in enumerate(loaded.config.ignore)
            if not rules.matching_codes(entry.rule)
        )
        if pending:
            raise ConfigError(pending, self.source)
        self._member_paths = sorted(self._members, key=len, reverse=True)
        self._base_loaded = (
            replace(loaded, config=base)
            if (base := self.config.workspace_base) is not None
            else None
        )
        self._validate_member_overrides()
        if validate:
            # The workspace base alone misses member policies; the full settings check them.
            self._validate_policies()
        lowered = [
            path
            for path, settings in self._members.items()
            if self._strict_guard()
            and (setting := settings.get(_STRICT_COVERAGE)) is not None
            and setting.value != "error"
        ]
        if lowered:
            raise ConfigError(
                [
                    ConfigIssue(
                        f"members.{path}.rules.{_STRICT_COVERAGE}",
                        "strict coverage must remain an error; use reasoned project.exclude entries instead",
                    )
                    for path in lowered
                ],
                self.source,
            )
        member_guards = [
            setting.location
            for settings in self._members.values()
            if (setting := settings.get(_STRICT_COVERAGE)) is not None
            and setting.enables
            and not self._strict_guard()
        ]
        if member_guards:
            raise ConfigError(
                [
                    ConfigIssue(
                        location,
                        "architecture/uncovered-source covers the whole workspace; enable it in the root rules",
                    )
                    for location in member_guards
                ],
                self.source,
            )
        guards = [
            layer[_STRICT_COVERAGE]
            for layer in self._layers
            if _STRICT_COVERAGE in layer
        ]
        if self._strict_guard() and guards[-1].value != "error":
            raise ConfigError(
                [
                    ConfigIssue(
                        guards[-1].location,
                        "architecture/uncovered-source is a guard and always reports errors; use error or off",
                    )
                ],
                self.source,
            )
        for name, settings in self._packages.items():
            if project_rules & settings.keys():
                raise ConfigError(
                    [
                        ConfigIssue(
                            f"style.overrides.{name}.rules",
                            "test-file and project rules must be configured at project level; use ignore.paths for individual test files",
                        )
                    ],
                    self.source,
                )

    def validate_packages(self, ctx: AnalysisContext) -> None:
        seen: dict[tuple[str, str], tuple[str, bool]] = {}
        issues: list[ConfigIssue] = []
        for contract in ctx.contracts.resolved:
            for code, setting in self._packages[contract.name].items():
                value = (setting.value, setting.broad and setting.value != "off")
                previous = seen.setdefault((contract.package, code), value)
                if previous != value:
                    issues.append(
                        ConfigIssue(
                            f"style.overrides.{contract.name}.rules",
                            f"conflicting {code} settings for {contract.package}",
                        )
                    )
        if issues:
            raise ConfigError(issues, self.source)
        self._validate_member_overrides(ctx)
        self._validate_scoped_selectors(ctx)

    def status(self, rule: RuleContract) -> RuleStatus:
        """Whether ``rule`` runs project-wide, only in some scopes, or not at all."""
        value = self.global_value(rule)
        if value not in (None, "off"):
            return RuleStatus("on", value)
        if self.has_package_activation(rule.code):
            return RuleStatus("scoped")
        activation = getattr(rule, "activation", None)
        if (
            value is None
            and activation is not None
            and activation.required
            and not activation.matches(self.config)
        ):
            return RuleStatus("needs policy")
        return RuleStatus("off")

    def has_package_activation(self, code: str) -> bool:
        """Whether a package contract or workspace member enables the rule locally."""
        return any(
            setting.enables
            for settings in (*self._packages.values(), *self._members.values())
            if (setting := settings.get(code)) is not None
        )

    def severity_for_path(
        self,
        rule: RuleContract,
        ctx: AnalysisContext,
        path: str | None,
        *,
        selected: bool = False,
    ) -> Severity | None:
        source = ctx.files.source_for_path(path) if path else None
        module = source.module if source else None
        if module is None and path:
            absolute = ctx.files.resolve_path(ctx.root / path)
            for source_root in self.config.project.source_roots:
                base = ctx.files.resolve_path(ctx.root / source_root)
                if absolute.is_relative_to(base):
                    parts = list(absolute.relative_to(base).with_suffix("").parts)
                    if parts and parts[0] in self.config.project.root_packages:
                        if parts[-1] == "__init__":
                            parts.pop()
                        module = ".".join(parts)
                        break
        return self.severity(rule, ctx, module, path=path, selected=selected)

    def severity(
        self,
        rule: RuleContract,
        ctx: AnalysisContext,
        module: str | None,
        *,
        path: str | None = None,
        selected: bool = False,
    ) -> Severity | None:
        return self.decision(rule, ctx, module, path=path, selected=selected).severity

    def decision(
        self,
        rule: RuleContract,
        ctx: AnalysisContext,
        module: str | None,
        *,
        path: str | None = None,
        selected: bool = False,
    ) -> RuleDecision:
        """Resolve a target's severity and the setting that supplied it."""
        value = rule.default_severity.value if self._default_enabled(rule) else None
        winner = None
        for setting in self._target_settings(rule.code, ctx, module=module, path=path):
            updated = setting.apply(value)
            if not setting.broad or setting.value == "off" or updated != value:
                winner = setting
            value = updated
        if value is None and selected:
            value = rule.default_severity.value
        severity = Severity(value) if value not in (None, "off") else None
        return (
            RuleDecision(severity, winner.location, winner.reason, source=winner.source)
            if winner
            else RuleDecision(severity)
        )

    def unused_policies(self) -> list[ConfigIssue]:
        """Declared policies whose rule no rules entry names, enabled or off."""
        return [
            ConfigIssue(
                f"rules.{rule.name}",
                f"{POLICIES[rule.code].description.rstrip('.')}, "
                f"but {rule.name} is not enabled; "
                f'add "{rule.name}: {rule.default_severity.value}" under rules, '
                'or "off" to keep the policy unchecked',
            )
            for rule, _ in self.policy_enablements()
        ]

    def policy_enablements(
        self,
    ) -> list[tuple[RuleContract, tuple[tuple[str, ...], ...]]]:
        """Unnamed rules with a declared policy, and the rules maps that enable them.

        Built-in defaults would let an optional policy's rule check every package,
        so a policy set only in style overrides is enabled in those overrides.
        """
        named = {
            code
            for layer in (
                *self._layers,
                *self._members.values(),
                *self._packages.values(),
            )
            for code, setting in layer.items()
            if not setting.broad or setting.value == "off"
        }
        root = self.config.workspace_base or self.config
        result: list[tuple[RuleContract, tuple[tuple[str, ...], ...]]] = []
        for rule in self.rules.rules:
            activation = POLICIES.get(rule.code)
            if (
                activation is None
                or getattr(rule, "enabled_by_default", False)
                or not any(activation.matches(policy) for policy in self._policies)
            ):
                continue
            option = activation.option
            accept = activation.accept or (lambda value: value is not None)
            root_declares_policy = (
                activation.matches(root)
                if option is None
                else accept(getattr(root.structure, option))
            )
            # A member or package exception leaves the root's policy in effect elsewhere.
            if rule.code in named and (
                not root_declares_policy or self.global_value(rule) is not None
            ):
                continue
            if root_declares_policy or activation.required or option is None:
                result.append((rule, (("rules",),)))
                continue
            result.append(
                (
                    rule,
                    tuple(
                        ("style", "overrides", name, "rules")
                        for name, contract in self.config.structure.contracts.items()
                        if accept(getattr(contract, option))
                    ),
                )
            )
        return result

    def migration_severity(self, rule: RuleContract, location: tuple[str, ...]) -> str:
        """Resolve caps on the old implicit activation before naming the rule."""
        default = rule.default_severity.value
        severity = self._apply_layers(rule.code, default)
        match location:
            case ("style", "overrides", name, "rules"):
                if (setting := self._packages[name].get(rule.code)) is not None:
                    severity = setting.apply(severity)
        return severity or default

    def global_value(self, rule: RuleContract) -> str | None:
        default = rule.default_severity.value if self._default_enabled(rule) else None
        return self._apply_layers(rule.code, default)

    def unconfigured_codes(self) -> set[str]:
        named = {
            code
            for layer in (
                *self._layers,
                *self._members.values(),
                *self._packages.values(),
            )
            for code, setting in layer.items()
            if not setting.broad
        }
        return {
            rule.code
            for rule in self.rules.rules
            if rule.code not in named and not self._default_enabled(rule)
        }

    def _target_settings(
        self,
        code: str,
        ctx: AnalysisContext,
        *,
        module: str | None,
        path: str | None,
    ) -> Iterator[_Setting]:
        for layer in self._layers:
            if (setting := layer.get(code)) is not None:
                yield setting
        if (member := self._member_for(ctx, module, path)) is not None and (
            setting := self._members[member].get(code)
        ) is not None:
            yield setting
        if module:
            candidates = sorted(
                (
                    contract
                    for contract in ctx.contracts.resolved
                    if module == contract.package
                    or module.startswith(contract.package + ".")
                ),
                key=lambda contract: len(contract.package),
            )
            for contract in candidates:
                if (setting := self._packages[contract.name].get(code)) is not None:
                    yield setting

    def _apply_layers(
        self, code: str, value: str | None, *, upto: int | None = None
    ) -> str | None:
        for layer in self._layers[:upto]:
            if (setting := layer.get(code)) is not None:
                value = setting.apply(value)
        return value

    def _validate_member_overrides(self, ctx: AnalysisContext | None = None) -> None:
        if self._base_loaded is None:
            return
        root = RuleSettings(self.rules, self._base_loaded, validate=False)
        root_ctx = None
        if ctx is not None:
            from smelt.analysis.context import AnalysisContext  # noqa: PLC0415

            root_ctx = AnalysisContext(ctx.root, root.config, store=ctx.store)
            root_ctx.files = replace(ctx.files, config=root.config)
        issues: list[ConfigIssue] = []
        members = self.config.members
        for member_path, member in members.items():
            maps: list[tuple[str, Mapping[str, RuleSetting], str | None]] = [
                (f"members.{member_path}.rules", member.rules, None)
            ]
            maps.extend(
                (
                    f"members.{member_path}.style.overrides.{name}.rules",
                    contract.rules,
                    contract.match,
                )
                for name, contract in member.structure.contracts.items()
            )
            for location, raw, pattern in maps:
                lowered: dict[str, list[RuleContract]] = {}
                entries = self._entries(raw, location, excluded=self._strict_guard())
                for code, setting in entries.items():
                    if code in self._strict_guard() or setting.reason is not None:
                        continue
                    rule = self.rules.by_code(code)
                    if rule is None:
                        continue
                    activation = getattr(rule, "activation", None)
                    option = getattr(activation, "option", None)
                    default = root.global_value(rule)
                    if (
                        option is not None
                        and getattr(root.config.structure, option) is None
                    ):
                        default = root._apply_layers(code, None)
                    values = [default or "off"]
                    if ctx is not None and root_ctx is not None:
                        values.extend(
                            severity.value if severity is not None else "off"
                            for source in ctx.files.sources.values()
                            if source.path.startswith(member_path + "/")
                            and (
                                pattern is None
                                or any(
                                    module_matches(
                                        pattern,
                                        ".".join(source.module.split(".")[:end]),
                                    )
                                    for end in range(
                                        1, len(source.module.split(".")) + 1
                                    )
                                )
                            )
                            for severity in [
                                root.severity(
                                    rule, root_ctx, source.module, path=source.path
                                )
                            ]
                        )
                    if _RANK[setting.value] < max(_RANK[value] for value in values):
                        lowered.setdefault(setting.selector, []).append(rule)
                issues.extend(
                    ConfigIssue(
                        f"{location}.{selector}",
                        self._weakening_message(selector, affected, raw[selector]),
                    )
                    for selector, affected in lowered.items()
                )
        if issues:
            raise ConfigError(issues, self.source)

    def _weakening_message(
        self, selector: str, affected: list[RuleContract], setting: RuleSetting
    ) -> str:
        if self._broad_selection(selector) is None:
            rule = affected[0]
            label = f"{rule.name} ({rule.code})"
        else:
            kind = "family " if selector != ALL_RULES else ""
            count = len(affected)
            label = f"{kind}{selector} ({count} {'rule' if count == 1 else 'rules'})"
        return f"lowering {label} below the inherited root severity requires {{severity: {setting}, reason: '...'}}"

    def _member_for(
        self, ctx: AnalysisContext, module: str | None, path: str | None
    ) -> str | None:
        """The innermost workspace member with a config that contains the file."""
        if not self._members:
            return None
        location = path or (ctx.files.path_for_module(module) if module else None)
        if location is None:
            return None
        return next(
            (
                member
                for member in self._member_paths
                if location == member or location.startswith(member + "/")
            ),
            None,
        )

    def _default_enabled(self, rule: RuleContract) -> bool:
        return bool(getattr(rule, "enabled_by_default", False))

    def _validate_policies(self) -> None:
        """An enabled rule whose policy is required must find that policy."""
        issues = []
        for rule in self.rules.rules:
            activation = getattr(rule, "activation", None)
            if activation is None or not activation.required:
                continue
            location = next(
                (
                    setting.location
                    for layer in (
                        *self._layers,
                        *self._members.values(),
                        *self._packages.values(),
                    )
                    if (setting := layer.get(rule.code)) is not None and setting.enables
                ),
                None,
            )
            if location is None or any(
                activation.matches(policy) for policy in self._policies
            ):
                continue
            issues.append(
                ConfigIssue(
                    location,
                    f"{rule.name} is enabled but its policy is missing; it requires that "
                    f"{activation.description[0].lower()}{activation.description[1:]}",
                )
            )
        if issues:
            raise ConfigError(issues, self.source)

    def _entries(
        self,
        raw: Mapping[str, RuleSetting],
        location: str,
        *,
        excluded: frozenset[str] = frozenset(),
        source: str | None = None,
        pending: list[ConfigIssue] | None = None,
    ) -> dict[str, _Setting]:
        """Resolve selectors to their winning settings, retaining reasons.

        ALL is the broadest selection, then a family, then a single
        rule, regardless of key order. Broad selections skip ``excluded`` codes,
        which only an individual entry can name.
        """
        result: dict[str, _Setting] = {}
        issues = self._limit_entry_issues(raw, location)
        # An entry that sets only max keeps the severity it inherits.
        raw = {key: value for key, value in raw.items() if _raw_value(value)}
        broad = sorted(
            (selection[0], key, selection[1])
            for key in raw
            if (selection := self._broad_selection(key)) is not None
        )
        for _, key, codes in broad:
            if _raw_value(raw[key]) == "on":
                issues.append(ConfigIssue(f"{location}.{key}", self._broad_help(key)))
                continue
            value = raw[key]
            for code in codes:
                if code not in excluded:
                    result[code] = _Setting(
                        key,
                        _raw_value(value) or "on",
                        broad=True,
                        location=f"{location}.{key}",
                        reason=value.reason
                        if isinstance(value, RuleOverride)
                        else None,
                        source=source,
                    )
        individual: set[str] = set()
        for key, value in raw.items():
            if self._broad_selection(key) is not None:
                continue
            named = self.codes.get(key)
            if named is None:
                issues.append(
                    ConfigIssue(
                        f"{location}.{key}",
                        f'unknown rule "{key}"{self._suggestion(key)}',
                    )
                )
            elif named in individual:
                issues.append(
                    ConfigIssue(f"{location}.{key}", f"{named} is already configured")
                )
            else:
                individual.add(named)
                severity = _raw_value(value) or "on"
                result[named] = _Setting(
                    key,
                    self._defaults[named] if severity == "on" else severity,
                    broad=False,
                    location=f"{location}.{key}",
                    reason=value.reason if isinstance(value, RuleOverride) else None,
                    source=source,
                )
        if pending is not None:
            pending.extend(issues)
        elif issues:
            raise ConfigError(issues, self.source)
        return result

    def _limit_entry_issues(
        self, raw: Mapping[str, RuleSetting], location: str
    ) -> list[ConfigIssue]:
        issues = []
        for key, value in raw.items():
            if not isinstance(value, RuleOverride) or value.max is None:
                continue
            where = f"{location}.{key}.max"
            if self._broad_selection(key) is not None:
                issues.append(
                    ConfigIssue(
                        where,
                        "max belongs to an individual rule; families and ALL can only disable or cap",
                    )
                )
            elif (code := self.codes.get(key)) is not None:
                issues.extend(_limit_issues(code, value.max, where))
            elif value.severity is None:
                issues.append(
                    ConfigIssue(
                        f"{location}.{key}",
                        f'unknown rule "{key}"{self._suggestion(key)}',
                    )
                )
        return issues

    def _root_limit_issues(self, loaded: LoadedConfig) -> list[ConfigIssue]:
        """A root entry that sets only max leaves its rule disabled."""
        named = {code for layer in self._layers for code in layer}
        return [
            ConfigIssue(
                f"rules.{key}",
                f"{key} is not enabled; add its severity, e.g. "
                f"{{severity: {self._defaults[code]}, max: {value.max}}}",
            )
            for layer in loaded.config.rule_layers
            for key, value in layer.items()
            if isinstance(value, RuleOverride)
            and value.severity is None
            and (code := self.codes.get(key)) is not None
            and code not in named
        ]

    def _strict_guard(self) -> frozenset[str]:
        """Strict coverage stays an error, so broad selections leave it alone."""
        if self.config.strict_coverage:
            return frozenset({_STRICT_COVERAGE})
        return frozenset()

    def _validate_scoped_selectors(self, ctx: AnalysisContext) -> None:
        config = self.config
        for name, member in config.members.items():
            if not any(self._broad_selection(key) for key in member.rules):
                continue
            paths = [
                path
                for path in ctx.files.all_python_paths()
                if path.startswith(name + "/")
            ]
            active = {
                rule.code
                for rule in self.rules.rules
                if any(
                    self.severity_for_path(rule, ctx, path) is not None
                    for path in paths
                )
            }
            self._validate_broad(
                member.rules,
                f"members.{name}.rules",
                self._members[name],
                active=active,
            )
        contracts = config.structure.contracts
        for name, contract in contracts.items():
            if not any(self._broad_selection(key) for key in contract.rules):
                continue
            packages = [
                resolved.package
                for resolved in ctx.contracts.resolved
                if resolved.name == name
            ]
            active = {
                rule.code
                for rule in self.rules.rules
                if any(
                    self.severity(rule, ctx, package) is not None
                    for package in packages
                )
            }
            self._validate_broad(
                contract.rules,
                f"style.overrides.{name}.rules",
                self._packages[name],
                active=active,
            )

    def _validate_root_broad(
        self, loaded: LoadedConfig, pending: list[ConfigIssue]
    ) -> None:
        """A root broad selector caps rules enabled before it or by any member or package."""
        scoped = {
            code
            for entries in (*self._members.values(), *self._packages.values())
            for code, setting in entries.items()
            if not setting.broad and setting.value != "off"
        }
        for index, layer in enumerate(loaded.config.rule_layers):
            active = scoped | {
                rule.code
                for rule in self.rules.rules
                if self._apply_layers(
                    rule.code,
                    rule.default_severity.value
                    if self._default_enabled(rule)
                    else None,
                    upto=index,
                )
                not in (None, "off")
            }
            try:
                self._validate_broad(layer, "rules", self._layers[index], active=active)
            except ConfigError as exc:
                pending.extend(exc.issues)

    def _validate_broad(
        self,
        raw: Mapping[str, RuleSetting],
        location: str,
        entries: Mapping[str, _Setting],
        *,
        active: set[str] | None = None,
    ) -> None:
        broad = {
            key: set(selection[1])
            for key in raw
            if (selection := self._broad_selection(key)) is not None
        }
        if not broad:
            return
        if active is None:
            active = {
                rule.code
                for rule in self.rules.rules
                if self.global_value(rule) not in (None, "off")
            }
        active.update(
            code
            for code, setting in entries.items()
            if not setting.broad and setting.value != "off"
        )
        issues = []
        for key, codes in broad.items():
            path = f"{location}.{key}"
            if _raw_value(raw[key]) != "off" and not active.intersection(codes):
                issues.append(ConfigIssue(path, self._broad_help(key)))
            self.selector_rules[path] = [
                rule.name
                for rule in self.rules.rules
                if rule.code in active
                and (setting := entries.get(rule.code)) is not None
                and setting.selector == key
            ]
        if issues:
            raise ConfigError(issues, self.source)

    def _broad_help(self, key: str) -> str:
        selection = self._broad_selection(key)
        codes = frozenset(selection[1]) if selection else frozenset()
        names = ", ".join(rule.name for rule in self.rules.rules if rule.code in codes)
        return f"a broad selector cannot enable rules in configuration; list the rules you want individually: {names}"

    def _broad_selection(self, key: str) -> tuple[int, tuple[str, ...]] | None:
        """Breadth rank and codes of ALL or a family, else None."""
        if key == ALL_RULES:
            return 0, tuple(self._defaults)
        if (family := self.rules.family_codes(key)) is not None:
            return 1, family
        return None

    def _suggestion(self, key: str) -> str:
        return _suggestion(key, self.codes)


def unknown_selectors(raw: Mapping[str, object], rules: RuleSet) -> list[ConfigIssue]:
    """Unknown rule selectors of an unvalidated config, to report with schema errors."""
    selectors = raw.get("rules")
    ignore = raw.get("ignore")
    found = [
        (f"rules.{key}", key)
        for key in (selectors if isinstance(selectors, dict) else {})
        if isinstance(key, str)
    ]
    found.extend(
        (f"ignore[{index}].rule", entry["rule"])
        for index, entry in enumerate(ignore if isinstance(ignore, list) else [])
        if isinstance(entry, dict) and isinstance(entry.get("rule"), str)
    )
    codes = _rule_codes(rules)
    return [
        ConfigIssue(location, f'unknown rule "{name}"{_suggestion(name, codes)}')
        for location, name in found
        if not rules.matching_codes(name)
    ]


def _rule_codes(rules: RuleSet) -> dict[str, str]:
    codes = {rule.code: rule.code for rule in rules.rules}
    codes.update({rule.name: rule.code for rule in rules.rules})
    return codes


def _suggestion(key: str, codes: dict[str, str]) -> str:
    families = [category.value for category in Category]
    return retired_hint(key, codes) or did_you_mean(key, [*codes, *families])


def _limit_issues(code: str, value: int, location: str) -> list[ConfigIssue]:
    if code not in _LIMIT_CODES:
        names = ", ".join(_LIMIT_CODES.values())
        return [
            ConfigIssue(location, f"this rule takes no max; max applies to {names}")
        ]
    minimum = _MINIMUM_MAX.get(code, 0)
    if value < minimum:
        return [ConfigIssue(location, f"max must be at least {minimum}")]
    return []


_ROOT_SCOPE = "this limit applies to the whole project; set it in the root rules"


def _raw_value(setting: RuleSetting) -> str | None:
    """The severity an entry sets; None when it only sets the rule's max."""
    return setting.severity if isinstance(setting, RuleOverride) else setting
