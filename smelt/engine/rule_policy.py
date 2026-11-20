"""Diagnose explicitly enabled rules missing the policy they enforce."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from smelt.model import is_within
from smelt.rules.meta import MissingRulePolicy

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

    from smelt.analysis.context import AnalysisContext
    from smelt.config.loader import LoadedConfig
    from smelt.diagnostics.violation import Severity, Violation
    from smelt.engine.rule_settings import RuleSettingOrigin, RuleSettings


def missing_rule_policies(
    ctx: AnalysisContext,
    loaded: LoadedConfig,
    settings: RuleSettings,
    *,
    codes: Collection[str],
    severity: Severity,
) -> Iterator[Violation]:
    """Report each effective named setting whose targets have no matching policy."""
    diagnostic = MissingRulePolicy()
    for rule in settings.rules.rules:
        required = _REQUIRED.get(rule.code)
        if (
            required is None
            or rule.code not in codes
            or not settings.has_explicit_activation(rule.code)
        ):
            continue
        covered = _covered_modules(ctx, rule.code, required.rsplit(".", 1)[-1])
        for origin in _uncovered_settings(ctx, settings, rule.code, covered=covered):
            source = (
                origin.source
                or loaded.source_for(f"{origin.location}.severity")
                or loaded.source_for(origin.location)
            )
            path = ctx.root / Path(source) if source else loaded.path
            key = _rule_key(ctx, path, origin.location)
            mark = key.start_mark if key is not None else None
            yield diagnostic.violation(
                f"{origin.location} enables {rule.name} without {required}",
                path=ctx.files.relative(path),
                line=mark.line + 1 if mark else None,
                column=mark.column + 1 if mark else None,
                expected={
                    "rule": rule.name,
                    "setting": origin.location,
                    "policy": required,
                },
                hint=f"Configure {required} for the intended scope or turn off the rule there.",
                edge=f"{rule.code}:{origin.location}",
            ).with_severity(severity)


_REQUIRED = {
    "ARC007": "architecture.packages.<name>.public",
    "IMP001": "architecture.packages.<name>.min_imports_to_bundle",
    "ARC008": "architecture.packages.<name>.min_importers_to_export",
    "IMP003": "style.imports",
}


def _covered_modules(ctx: AnalysisContext, code: str, option: str) -> set[str]:
    modules = ctx.files.sources.keys()
    if code == "IMP003":
        return {module for module in modules if ctx.contracts.style_for(module)}
    packages = {
        contract.package
        for contract in ctx.contracts.resolved
        if getattr(contract.spec, option) is not None
    }
    if code == "ARC008":
        # Export gap findings are anchored at definitions within the contract.
        return {
            module for module in modules if any(is_within(module, p) for p in packages)
        }
    # Public and bundling policies constrain outside callers across the project.
    return set(modules) if packages else set()


def _uncovered_settings(
    ctx: AnalysisContext, settings: RuleSettings, code: str, *, covered: set[str]
) -> Iterator[RuleSettingOrigin]:
    origins: dict[RuleSettingOrigin, bool] = {}
    for source in ctx.files.sources.values():
        origin = settings.explicit_activation(
            code, ctx, module=source.module, path=source.path
        )
        if origin is not None:
            origins[origin] = origins.get(origin, False) or source.module in covered
    for origin, has_policy in origins.items():
        if not has_policy:
            yield origin


def _rule_key(
    ctx: AnalysisContext, path: Path, location: str
) -> yaml.ScalarNode | None:
    try:
        loader = yaml.SafeLoader(ctx.store.read_text(path))
    except (OSError, UnicodeError, yaml.YAMLError):
        return None
    try:
        document = loader.get_single_node()
        # A standalone member file spells its rules and overrides at the top level.
        local = location.removeprefix(f"members.{ctx.files.relative(path.parent)}.")
        return _mapping_key(document, location, loader) or _mapping_key(
            document, local, loader
        )
    except yaml.YAMLError:
        return None
    finally:
        loader.dispose()


def _mapping_key(
    node: yaml.Node | None, location: str, loader: yaml.SafeLoader
) -> yaml.ScalarNode | None:
    if isinstance(node, yaml.MappingNode):
        loader.flatten_mapping(node)
        for key, child in reversed(node.value):
            if not isinstance(key, yaml.ScalarNode):
                continue
            if key.value == location:
                return key
            prefix = key.value + "."
            if (
                location.startswith(prefix)
                and (
                    found := _mapping_key(child, location.removeprefix(prefix), loader)
                )
                is not None
            ):
                return found
    return None
