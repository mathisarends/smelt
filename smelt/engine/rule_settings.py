"""Resolve the same rule names for project and package settings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from smelt.config.errors import ConfigError, ConfigIssue, did_you_mean
from smelt.config.models import ALL_RULES, RuleOverride
from smelt.config.patterns import module_matches
from smelt.config.workspace import architecture_scopes
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.renames import retired_hint

if TYPE_CHECKING:
    from collections.abc import Mapping

    from smelt.analysis.context import AnalysisContext
    from smelt.config.loader import LoadedConfig
    from smelt.config.models import RuleSetting
    from smelt.rules.base import RuleContract, RuleSet

_STRICT_COVERAGE = "SMT306"
_RANK = {"off": 0, "hint": 1, "warning": 2, "error": 3}


@dataclass(frozen=True, slots=True)
class _Setting:
    selector: str
    value: str
    broad: bool
    reasoned: bool

    def apply(self, inherited: str | None) -> str | None:
        if not self.broad or self.value == "off":
            return self.value
        if inherited is None:
            return None
        return min(inherited, self.value, key=_RANK.__getitem__)


class RuleSettings:
    def __init__(
        self, rules: RuleSet, loaded: LoadedConfig, *, validate_selectors: bool = True
    ) -> None:
        self.config = loaded.config
        # A member architecture's policy activates its rules like the root's does.
        self._policies = (
            self.config,
            *(scope.config for scope in architecture_scopes(self.config)[1:]),
        )
        self.source = loaded.path.name
        self.rules = rules
        self.codes = {rule.code: rule.code for rule in rules.rules}
        self.codes.update({rule.name: rule.code for rule in rules.rules})
        self.defaults = {rule.code: rule.default_severity.value for rule in rules.rules}
        project_rules = {
            rule.code
            for rule in rules.rules
            if getattr(rule, "scope", "source") != "source"
        }
        # A later layer replaces what an earlier one set: the level wins over specificity.
        self._layers: list[dict[str, _Setting]] = []
        self.selector_rules: dict[str, list[str]] = {}
        for layer in loaded.config.rule_layers:
            entries = self._entries(layer, "rules", excluded=self._strict_guard())
            if validate_selectors:
                self._validate_broad(layer, "rules", entries)
            self._layers.append(entries)
        self._packages = {
            name: self._entries(
                contract.rules,
                f"structure.contracts.{name}.rules",
                excluded=frozenset(project_rules),
            )
            for name, contract in self.config.structure.contracts.items()
        }
        # Workspace members refine the root by path, so their tests are covered too.
        self._members = {
            path: self._entries(
                member.rules, f"members.{path}.rules", excluded=self._strict_guard()
            )
            for path, member in loaded.config.members.items()
        }
        self._member_paths = sorted(self._members, key=len, reverse=True)
        self._base_loaded = (
            replace(loaded, config=base)
            if (base := self.config.workspace_base) is not None
            else None
        )
        self._validate_member_overrides()
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
        for index, entry in enumerate(loaded.config.ignore):
            if not rules.matching_codes(entry.rule):
                raise ConfigError(
                    [
                        ConfigIssue(
                            f"ignore[{index}].rule",
                            f'unknown rule "{entry.rule}"{self._suggestion(entry.rule)}',
                        )
                    ],
                    self.source,
                )
        for name, settings in self._packages.items():
            if project_rules & settings.keys():
                raise ConfigError(
                    [
                        ConfigIssue(
                            f"structure.contracts.{name}.rules",
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
                            f"structure.contracts.{contract.name}.rules",
                            f"conflicting {code} settings for {contract.package}",
                        )
                    )
        if issues:
            raise ConfigError(issues, self.source)
        self._validate_member_overrides(ctx)
        self._validate_scoped_selectors(ctx)

    def has_package_activation(self, code: str) -> bool:
        """Whether a package contract or workspace member enables the rule locally."""
        return any(
            not setting.broad and setting.value != "off"
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
        """Root setting, then the member's, then the closest package contract."""
        default = (
            rule.default_severity.value
            if self._enabled_for(rule, ctx, module)
            else None
        )
        value = self._apply_layers(rule.code, default)
        if (member := self._member_for(ctx, module, path)) is not None and (
            setting := self._members[member].get(rule.code)
        ) is not None:
            value = setting.apply(value)
        if module:
            applicable = sorted(
                (
                    contract
                    for contract in ctx.contracts.resolved
                    if module == contract.package
                    or module.startswith(contract.package + ".")
                ),
                key=lambda contract: len(contract.package),
            )
            for contract in applicable:
                if (
                    setting := self._packages[contract.name].get(rule.code)
                ) is not None:
                    value = setting.apply(value)
        if value is None and selected:
            return rule.default_severity
        return Severity(value) if value not in (None, "off") else None

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

    def _apply_layers(self, code: str, value: str | None) -> str | None:
        for layer in self._layers:
            if (setting := layer.get(code)) is not None:
                value = setting.apply(value)
        return value

    def _validate_member_overrides(self, ctx: AnalysisContext | None = None) -> None:
        if self._base_loaded is None:
            return
        root = RuleSettings(self.rules, self._base_loaded, validate_selectors=False)
        root_ctx = None
        if ctx is not None:
            from smelt.analysis.context import AnalysisContext  # noqa: PLC0415

            root_ctx = AnalysisContext(ctx.root, root.config, store=ctx.store)
            root_ctx.files = replace(ctx.files, config=root.config)
        issues: dict[str, ConfigIssue] = {}
        members = self.config.members
        for member_path, member in members.items():
            maps: list[tuple[str, Mapping[str, RuleSetting], str | None]] = [
                (f"members.{member_path}.rules", member.rules, None)
            ]
            maps.extend(
                (
                    f"members.{member_path}.structure.contracts.{name}.rules",
                    contract.rules,
                    contract.match,
                )
                for name, contract in member.structure.contracts.items()
            )
            for location, raw, pattern in maps:
                entries = self._entries(raw, location, excluded=self._strict_guard())
                for code, setting in entries.items():
                    if code in self._strict_guard() or setting.reasoned:
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
                        key = f"{location}.{code}"
                        issues[key] = ConfigIssue(
                            key,
                            f"lowering {code} below the inherited root severity requires {{severity: {setting.value}, reason: '...'}}",
                        )
        if issues:
            raise ConfigError(list(issues.values()), self.source)

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

    def _enabled_for(
        self,
        rule: RuleContract,
        ctx: AnalysisContext,
        module: str | None,
    ) -> bool:
        """An option-scoped activation applies only where structure or a contract sets it."""
        activation = getattr(rule, "activation", None)
        if (
            activation is None
            or activation.option is None
            or getattr(rule, "enabled_by_default", False)
            or module is None
        ):
            return self._default_enabled(rule)
        return (
            activation.matches(self.config)
            and ctx.contracts.policy_for(module, activation.option) is not None
        )

    def _default_enabled(self, rule: RuleContract) -> bool:
        activation = getattr(rule, "activation", None)
        return bool(
            getattr(rule, "enabled_by_default", False)
            or (
                activation is not None
                and any(activation.matches(policy) for policy in self._policies)
            )
        )

    def _entries(
        self,
        raw: Mapping[str, RuleSetting],
        location: str,
        *,
        excluded: frozenset[str] = frozenset(),
    ) -> dict[str, _Setting]:
        """Resolve selectors to their winning settings, retaining reasons.

        ALL is the broadest selection, then a family, then a single
        rule, regardless of key order. Broad selections skip ``excluded`` codes,
        which only an individual entry can name.
        """
        result: dict[str, _Setting] = {}
        issues: list[ConfigIssue] = []
        broad = sorted(
            (selection[0], key, selection[1])
            for key in raw
            if (selection := self._broad_selection(key)) is not None
        )
        for _, key, codes in broad:
            if _raw_value(raw[key]) == "on":
                issues.append(ConfigIssue(f"{location}.{key}", self._broad_help(key)))
                continue
            for code in codes:
                if code not in excluded:
                    result[code] = _Setting(
                        key,
                        _raw_value(raw[key]),
                        True,
                        isinstance(raw[key], RuleOverride),
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
                severity = _raw_value(value)
                result[named] = _Setting(
                    key,
                    self.defaults[named] if severity == "on" else severity,
                    False,
                    isinstance(value, RuleOverride),
                )
        if issues:
            raise ConfigError(issues, self.source)
        return result

    def _strict_guard(self) -> frozenset[str]:
        """Strict coverage stays an error, so broad selections leave it alone."""
        if self.config.project.coverage == "strict":
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
                f"structure.contracts.{name}.rules",
                self._packages[name],
                active=active,
            )

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
            return 0, tuple(self.defaults)
        if (family := self.rules.family_codes(key)) is not None:
            return 1, family
        return None

    def _suggestion(self, key: str) -> str:
        families = [category.value for category in Category]
        return retired_hint(key, self.codes) or did_you_mean(
            key, [*self.codes, *families]
        )


def _raw_value(setting: RuleSetting) -> str:
    return setting.severity if isinstance(setting, RuleOverride) else setting
