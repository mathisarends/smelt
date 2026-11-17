"""Resolve the same rule names for project and package settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.config.errors import ConfigError, ConfigIssue, did_you_mean
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.groups import RULE_GROUPS
from smelt.rules.renames import retired_hint

if TYPE_CHECKING:
    from collections.abc import Mapping

    from smelt.analysis.context import AnalysisContext
    from smelt.config.loader import LoadedConfig
    from smelt.rules.base import RuleContract, RuleSet


class RuleSettings:
    def __init__(self, rules: RuleSet, loaded: LoadedConfig) -> None:
        self.config = loaded.config
        self.source = loaded.path.name
        self.rules = rules
        self.codes = {rule.code: rule.code for rule in rules.rules}
        self.codes.update({rule.name: rule.code for rule in rules.rules})
        project_rules = {
            rule.code
            for rule in rules.rules
            if getattr(rule, "scope", "source") != "source"
        }
        self.global_rules = self._normalize(loaded.config.rules, "rules")
        self.packages = {
            name: self._normalize(contract.rules, f"structure.contracts.{name}.rules")
            for name, contract in loaded.config.structure.contracts.items()
        }
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
        for name, settings in self.packages.items():
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
        seen: dict[tuple[str, str], str] = {}
        issues: list[ConfigIssue] = []
        for contract in ctx.contracts.resolved:
            for code, value in self.packages[contract.name].items():
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

    def has_package_activation(self, code: str) -> bool:
        return any(
            settings.get(code, "off") != "off" for settings in self.packages.values()
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
        return self.severity(rule, ctx, module, selected=selected)

    def severity(
        self,
        rule: RuleContract,
        ctx: AnalysisContext,
        module: str | None,
        *,
        selected: bool = False,
    ) -> Severity | None:
        value = self.global_rules.get(rule.code)
        if value is None:
            value = (
                rule.default_severity.value
                if self._enabled_for(rule, ctx, module, selected=selected)
                else "off"
            )
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
                value = self.packages[contract.name].get(rule.code, value)
        return None if value == "off" else Severity(value)

    def globally_enabled(self, rule: RuleContract, *, selected: bool = False) -> bool:
        setting = self.global_rules.get(rule.code)
        if setting is not None:
            return setting != "off"
        activation = getattr(rule, "activation", None)
        return bool(
            selected
            or getattr(rule, "enabled_by_default", False)
            or (activation is not None and activation.matches(self.config))
        )

    def _enabled_for(
        self,
        rule: RuleContract,
        ctx: AnalysisContext,
        module: str | None,
        *,
        selected: bool,
    ) -> bool:
        """An option-scoped activation applies only where structure or a contract sets it."""
        activation = getattr(rule, "activation", None)
        if (
            activation is None
            or activation.option is None
            or selected
            or getattr(rule, "enabled_by_default", False)
            or module is None
        ):
            return self.globally_enabled(rule, selected=selected)
        return (
            activation.matches(self.config)
            and ctx.contracts.policy_for(module, activation.option) is not None
        )

    def _normalize(self, raw: Mapping[str, str], location: str) -> dict[str, str]:
        result: dict[str, str] = {}
        issues: list[ConfigIssue] = []
        # A family is the broadest selection, then a group, then a single rule.
        for key, value in raw.items():
            if (family := self.rules.family_codes(key)) is not None:
                result.update(dict.fromkeys(family, value))
        for key, value in raw.items():
            if group := RULE_GROUPS.get(key):
                result.update(dict.fromkeys(group.codes, value))
        individual: set[str] = set()
        for key, value in raw.items():
            if key in RULE_GROUPS or self.rules.family_codes(key) is not None:
                continue
            code = self.codes.get(key)
            if code is None:
                issues.append(
                    ConfigIssue(
                        f"{location}.{key}",
                        f'unknown rule "{key}"{self._suggestion(key)}',
                    )
                )
            elif code in individual:
                issues.append(
                    ConfigIssue(f"{location}.{key}", f"{code} is already configured")
                )
            else:
                individual.add(code)
                result[code] = value
        if issues:
            raise ConfigError(issues, self.source)
        return result

    def _suggestion(self, key: str) -> str:
        families = [category.value for category in Category]
        return retired_hint(key, self.codes) or did_you_mean(
            key, [*self.codes, *RULE_GROUPS, *families]
        )
