from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from smelt.analysis.coverage import is_governed
from smelt.config.errors import did_you_mean
from smelt.config.loader import LoadedConfig
from smelt.config.models import PackagePolicy
from smelt.config.patterns import module_matches
from smelt.diagnostics.violation import Category, Severity
from smelt.engine.coverage import policy_hash
from smelt.engine.rule_settings import RuleSettings
from smelt.model import ModuleKind, is_within
from smelt.rules.registry import build_rule_set
from smelt.rules.testing.location import mirror_path

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext
    from smelt.analysis.contracts import PolicyOption
    from smelt.config.models import ThirdPartyPolicy
    from smelt.diagnostics.report import Report
    from smelt.model import ArchitectureModel, ModuleInfo
    from smelt.rules.base import RuleContract


class TargetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Target:
    kind: str  # project | feature | module | package
    feature: str | None = None
    module: str | None = None
    path: str | None = None
    planned: bool = False


@dataclass(frozen=True, slots=True)
class LayerBrief:
    name: str
    package: str | None
    may_depend_on: tuple[str, ...]
    third_party: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "package": self.package,
            "may_depend_on": list(self.may_depend_on),
            "third_party": self.third_party,
        }


@dataclass(frozen=True, slots=True)
class Briefing:
    target: Target
    package: str | None
    module_kind: ModuleKind | None
    layer: str | None
    features: tuple[str, ...]
    layers: tuple[LayerBrief, ...]
    cross_feature: str | None
    shared: tuple[str, ...]
    composition_root: tuple[str, ...]
    wiring: tuple[str, ...]
    central: bool
    tests: tuple[str, ...]
    is_wiring: bool = False
    boundary_policy: str = ""
    transitive: bool = False
    type_checking: str = "include"
    cycles: tuple[str, ...] = ()
    type_checking_cycles: str = "include"
    canonical_exports: dict[str, str] = field(default_factory=dict)
    file_naming: dict[str, str] = field(default_factory=dict)
    # cross_feature as fields: default and allow entries, None feature = every feature
    cross_feature_policy: dict[str, Any] | None = None
    policy_hash: str = ""
    analysis_error: str | None = None
    violations: dict[str, int] = field(default_factory=dict)
    violation_codes: tuple[str, ...] = ()
    coverage: dict[str, Any] = field(default_factory=dict)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    import_style: dict[str, Any] | None = None
    header_policy: dict[str, Any] = field(default_factory=dict)
    naming_policy: dict[str, Any] = field(default_factory=dict)
    ordering_policy: dict[str, Any] | None = None
    python_target: dict[str, Any] = field(default_factory=dict)
    test_policy: dict[str, Any] = field(default_factory=dict)
    type_dispatch_policy: dict[str, Any] | None = None
    rules: dict[str, str] = field(default_factory=dict)
    rule_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    style: dict[str, dict[str, Any]] = field(default_factory=dict)
    configuration_warnings: tuple[str, ...] = ()
    workspace_policy: _WorkspacePolicy | None = None
    layer_assignment: dict[str, str | bool] | None = None

    @property
    def feature_planned(self) -> bool:
        return (
            self.target.planned
            and self.target.feature is not None
            and self.target.feature not in self.features
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "scope": self.target.kind,
            "feature": self.target.feature,
            "feature_planned": self.feature_planned,
            "module": self.target.module,
            "path": self.target.path,
            "package": self.package,
            "module_kind": self.module_kind.value if self.module_kind else None,
            "layer": self.layer,
            "layer_assignment": self.layer_assignment,
            "central": self.central,
            "is_wiring": self.is_wiring,
            "planned": self.target.planned,
            "boundary_policy": self.boundary_policy,
            "coverage": self.coverage,
            "contracts": self.contracts,
            "canonical_exports": self.canonical_exports,
            "file_naming": self.file_naming,
            "import_style": self.import_style,
            "header_policy": self.header_policy,
            "naming_policy": self.naming_policy,
            "ordering_policy": self.ordering_policy,
            "python_target": self.python_target,
            "test_policy": self.test_policy,
            "type_dispatch_policy": self.type_dispatch_policy,
            "rules": self.rules,
            "rule_details": self.rule_details,
            "style": self.style,
            "configuration_warnings": list(self.configuration_warnings),
            "member": self.workspace_policy.member if self.workspace_policy else None,
            "architecture_source": self.workspace_policy.architecture
            if self.workspace_policy
            else None,
            "rule_sources": list(self.workspace_policy.rules)
            if self.workspace_policy
            else [],
            "policy_hash": self.policy_hash,
            "imports": {
                "transitive": self.transitive,
                "type_checking": self.type_checking,
                "cycles": list(self.cycles),
                "type_checking_cycles": self.type_checking_cycles,
            },
            "features": list(self.features),
            "layers": [layer.to_json() for layer in self.layers],
            "cross_feature": self.cross_feature,
            "cross_feature_policy": self.cross_feature_policy,
            "shared": list(self.shared),
            "composition_root": list(self.composition_root),
            "wiring": list(self.wiring),
            "tests": list(self.tests),
            "violations": None
            if self.analysis_error
            else {**self.violations, "codes": list(self.violation_codes)},
            "analysis_error": self.analysis_error,
        }


def resolve_target(ctx: AnalysisContext, raw: str | None) -> Target:
    """A feature name or a project-relative path (file or directory)."""
    if raw is None or raw.strip("/") in ("", "."):
        return Target("project")
    model = ctx.model
    if raw in model.features:
        return Target("feature", feature=raw)
    path = raw.strip("/")
    files = ctx.files
    module_path = files.path_for_module(raw)
    if module_path is not None:
        path = module_path
    source = files.source_for_path(path)
    if source is not None:
        info = model.info(source.module)
        feature = info.feature if info else None
        return Target("module", feature=feature, module=source.module, path=path)
    for package in files.packages.values():
        if package.path == path:
            info = model.info(package.module)
            feature = info.feature if info else None
            return Target("package", feature, package.module, path)
    inventory = next(
        (
            entry
            for entry in ctx.coverage.entries
            if entry.path == path or entry.module == raw
        ),
        None,
    )
    if inventory is not None:
        return Target("module", module=inventory.module, path=inventory.path)
    planned = PurePosixPath(path)
    if not ctx.store.exists(ctx.root / path) and planned.suffix == ".py":
        for parent in planned.parents:
            ancestor = next(
                (p for p in files.packages.values() if p.path == str(parent)), None
            )
            if ancestor is None:
                continue
            segments = planned.relative_to(parent).with_suffix("").parts
            if all(segment.isidentifier() for segment in segments):
                is_package = planned.name == "__init__.py"
                relative = segments[:-1] if is_package else segments
                module = ".".join((ancestor.module, *relative))
                info = model.planned_info(module, is_package=is_package)
                return Target(
                    "module", info.feature if info else None, module, path, planned=True
                )
    candidates = [*model.features, *(p.path for p in files.packages.values())]
    msg = (
        f'"{raw}" is neither a feature nor a source path{did_you_mean(raw, candidates)}'
    )
    if model.features:
        msg += f"; features: {', '.join(sorted(model.features))}"
    raise TargetError(msg)


def build_briefing(
    ctx: AnalysisContext,
    target: Target,
    report: Report | None,
    *,
    analysis_error: str | None = None,
    loaded: LoadedConfig | None = None,
) -> Briefing:
    ctx, architecture_key = _governing_view(ctx, target)
    model = ctx.model
    arch = ctx.config.architecture
    info = _target_info(model, target)
    package = None
    if target.feature is not None:
        package = model.feature_package_for(target.feature)
    layers = tuple(
        LayerBrief(
            name,
            model.layer_package(target.feature, name)
            if target.feature is not None or not model.has_features
            else None,
            tuple(layer.may_depend_on),
            _third_party(layer.third_party),
        )
        for name, layer in model.layers.items()
    )
    scoped = (
        [v for v in report.violations if _violation_in(ctx, v.path, target)]
        if report
        else []
    )
    exempt = bool(info and (info.wiring or info.kind is ModuleKind.COMPOSITION_ROOT))
    cross_feature = _cross_feature(model) if model.has_features else None
    coverage = _coverage_brief(ctx, target, info=info)
    target_path = target.path or ctx.files.path_for_module(package or "")
    module = target.module or package
    policy_file = loaded or LoadedConfig(ctx.config, ctx.root / "smelt.yml")
    workspace_policy = _workspace_sources(
        ctx, target_path, loaded=loaded, module=module
    )
    style = _style_brief(ctx, module, policy_file)
    rules, rule_details = _rule_brief(
        ctx, policy_file, module=module, path=target_path, style=style
    )
    if info and (info.central or info.kind is ModuleKind.SHARED):
        cross_feature = "none (must not import features)"
    elif exempt:
        cross_feature = "allowed (wiring/composition-root exception)"
    return Briefing(
        target=target,
        package=package,
        module_kind=info.kind if info else None,
        layer=info.layer if info else None,
        layer_assignment=_layer_assignment(info, policy_file, architecture_key),
        features=tuple(sorted(model.features)),
        layers=layers,
        cross_feature=cross_feature,
        shared=tuple(arch.shared),
        composition_root=tuple(arch.composition_root),
        wiring=_wiring(ctx, package or target.module),
        central=bool(info and info.central),
        tests=_tests(ctx, target),
        is_wiring=bool(info and info.wiring),
        boundary_policy=_covered_policy(info, coverage),
        transitive=arch.import_graph.transitive,
        type_checking=arch.import_graph.type_checking,
        cycles=tuple(arch.import_graph.cycles),
        type_checking_cycles=arch.import_graph.type_checking_cycles,
        canonical_exports=dict(ctx.config.structure.canonical_exports),
        file_naming=ctx.config.structure.file_naming.model_dump(exclude_none=True),
        cross_feature_policy={
            "default": arch.cross_feature.default,
            "allow": arch.cross_feature.entries(),
        }
        if model.has_features
        else None,
        policy_hash=policy_hash(ctx.config),
        analysis_error=analysis_error,
        violations={
            "errors": sum(v.severity is Severity.ERROR for v in scoped),
            "warnings": sum(v.severity is Severity.WARNING for v in scoped),
            "hints": sum(v.severity is Severity.HINT for v in scoped),
        },
        violation_codes=tuple(sorted({v.code for v in scoped})),
        coverage=coverage,
        contracts=ctx.contracts.describe(target.module or package),
        import_style=ctx.contracts.style_brief(target.module or package),
        header_policy=ctx.contracts.header_brief(target.module or package),
        naming_policy=ctx.contracts.naming_brief(target.module or package),
        ordering_policy=ctx.contracts.ordering_brief(target.module or package),
        test_policy=ctx.config.tests.model_dump(mode="json"),
        type_dispatch_policy=ctx.contracts.dispatch_brief(target.module or package),
        rules=rules,
        rule_details=rule_details,
        style=style,
        configuration_warnings=tuple(str(issue) for issue in policy_file.warnings),
        workspace_policy=workspace_policy,
        python_target=ctx.python_targets.for_path(
            ctx.root / target_path if target_path else None
        ).to_json(),
    )


def render_briefing(briefing: Briefing, ctx: AnalysisContext) -> str:
    lines = _header(briefing, ctx)
    lines.extend(
        f"Configuration warning: {warning}"
        for warning in briefing.configuration_warnings
    )
    lines.append("")
    lines.append(
        textwrap.fill(
            briefing.boundary_policy,
            width=88,
            initial_indent="Policy: ",
            subsequent_indent="        ",
        )
    )
    coverage = briefing.coverage
    if (assignment := briefing.layer_assignment) is not None:
        lines.append(
            f"Layer assignment: {assignment['source']} {assignment['key']} "
            f"(matches {assignment['prefix']})"
        )
    lines.append(
        textwrap.fill(
            f"Coverage: {coverage.get('mode', 'configured')}; "
            + (
                f"{coverage['status']}. {coverage.get('reason', '')}"
                if "status" in coverage
                else f"{'complete' if coverage.get('complete') else 'incomplete'} policy assignment for {coverage.get('files', 0)} source files."
            ),
            width=88,
        )
    )
    lines.append(
        "Imports: direct and transitive dependencies checked (layers, features, shared, composition roots, third-party)"
        if briefing.transitive
        else "Imports: direct only (indirect chains require architecture.import_graph.transitive: true)"
    )
    lines.extend(_contract_lines(briefing))
    if briefing.type_checking_cycles != "include":
        lines.append(f"Type-checking cycle edges: {briefing.type_checking_cycles}")
    lines.append("")
    if briefing.module_kind is ModuleKind.UNCLASSIFIED:
        lines.extend((*_wrap_tests(briefing.tests), "", _violation_line(briefing)))
        return "\n".join(lines) + "\n"
    if briefing.layers:
        lines.extend(_layer_lines(briefing))
    if briefing.cross_feature is not None:
        lines.append(f"Cross-feature:    {briefing.cross_feature}")
    if briefing.shared:
        lines.append(
            f"Shared:           {', '.join(briefing.shared)} (must not import features)"
        )
    if briefing.composition_root:
        lines.append(f"Composition root: {', '.join(briefing.composition_root)}")
    if briefing.wiring:
        lines.append(f"Wiring:           {', '.join(briefing.wiring)}")
    lines.extend(("", *_wrap_tests(briefing.tests), "", _violation_line(briefing)))
    return "\n".join(lines) + "\n"


def _governing_view(
    ctx: AnalysisContext, target: Target
) -> tuple[AnalysisContext, str]:
    """The governing architecture view and its config key."""
    model = ctx.model
    if not model.parts:
        return ctx, "architecture"
    module = target.module
    if module is None and target.feature is not None:
        module = model.feature_package_for(target.feature)
    if module is None:
        return ctx, "architecture"
    part = model.part_for(module)
    if part is model:
        return ctx, "architecture"
    index = model.parts.index(part)
    scope = ctx.architecture_scopes[index]
    return ctx.architecture_view(scope), scope.location


@dataclass(frozen=True, slots=True)
class _WorkspacePolicy:
    member: str
    architecture: str | None
    rules: tuple[str, ...]


def _workspace_sources(
    ctx: AnalysisContext,
    path: str | None,
    *,
    loaded: LoadedConfig | None,
    module: str | None,
) -> _WorkspacePolicy | None:
    if loaded is None:
        return None
    if path is None and module is not None and module in ctx.files.packages:
        path = ctx.files.packages[module].path
    member = ctx.workspace.member_for_path(path)
    if member is None:
        return None
    root_config = loaded.path.name
    inputs = (
        list(loaded.inputs[: loaded.inputs.index(loaded.path) + 1])
        if loaded.path in loaded.inputs
        else [loaded.path]
    )
    member_file = loaded.origins.get(f"members.{member}")
    if member_file is not None:
        inputs.append(Path(member_file))
    config = loaded.config.members.get(member)
    architecture = (
        _config_source(Path(member_file), root=loaded.root)
        if member_file is not None
        and config is not None
        and config.governs_architecture
        else root_config
        if member == "." and loaded.config.architecture.has_layout
        else None
    )
    sources = [_config_source(path, root=loaded.root) for path in inputs]
    if module is not None:
        sources.extend(
            f"style.overrides ({contract.name})"
            for contract in sorted(
                ctx.contracts.resolved, key=lambda item: len(item.package)
            )
            if contract.spec.rules and is_within(module, contract.package)
        )
    return _WorkspacePolicy(member, architecture, tuple(sources))


def _style_brief(
    ctx: AnalysisContext, module: str | None, loaded: LoadedConfig
) -> dict[str, dict[str, Any]]:
    settings: dict[str, dict[str, Any]] = {}
    for name in PackagePolicy.model_fields:
        contract = ctx.contracts.policy_for(module or "", cast("PolicyOption", name))
        if contract is None:
            continue
        spec = contract.spec
        location = f"{spec.style_location or 'style'}.{name}"
        value = spec.model_dump(mode="json", include={name}, by_alias=True)[name]
        reason = spec.reasons.get(name)
        settings[name] = {
            "value": value["value"] if reason is not None else value,
            "key": location,
            "source": _policy_source(loaded, location),
            "reason": reason,
        }
    return settings


def _rule_brief(
    ctx: AnalysisContext,
    loaded: LoadedConfig,
    *,
    module: str | None,
    path: str | None,
    style: dict[str, dict[str, Any]],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    rules = build_rule_set()
    policy = RuleSettings(rules, loaded)
    severities: dict[str, str] = {}
    details: dict[str, dict[str, Any]] = {}
    for rule in rules.rules:
        decision = policy.decision(rule, ctx, module, path=path)
        severity = decision.severity.value if decision.severity else "off"
        severities[rule.name] = severity
        if decision.severity is None and decision.location is None:
            continue
        entry: dict[str, Any] = {
            "severity": severity,
            "scope": getattr(rule, "scope", "source"),
            "family": rule.category.value,
        }
        if decision.location:
            entry.update(
                key=decision.location,
                source=_policy_source(
                    loaded, decision.location, source=decision.source
                ),
                reason=decision.reason,
                activation="rules",
            )
        else:
            entry.update(
                _activation_brief(
                    rule,
                    loaded,
                    style=style,
                    member=ctx.workspace.member_for_path(path),
                )
            )
        details[rule.name] = entry
    return severities, details


def _activation_brief(
    rule: RuleContract,
    loaded: LoadedConfig,
    *,
    style: dict[str, dict[str, Any]],
    member: str | None,
) -> dict[str, Any]:
    activation = getattr(rule, "activation", None)
    if getattr(rule, "enabled_by_default", False) or activation is None:
        return {"activation": "baseline"}
    if (option := style.get(activation.option)) is not None:
        return {
            "activation": "policy",
            "key": option["key"],
            "source": option["source"],
            "reason": option["reason"],
        }
    for pattern in rule.explain().config:
        key = pattern.split("<", maxsplit=1)[0].rstrip(".")
        if member and member != "." and getattr(rule, "architecture_scoped", False):
            key = f"members.{member}.{key}"
        if key in loaded.origins:
            return {
                "activation": "policy",
                "key": key,
                "source": _policy_source(loaded, key),
            }
    return {"activation": "policy", "description": activation.description}


def _layer_assignment(
    info: ModuleInfo | None, loaded: LoadedConfig, architecture_key: str
) -> dict[str, str | bool] | None:
    if info is None or info.layer_binding is None:
        return None
    binding = info.layer_binding
    key = f"{architecture_key}.{binding.key}"
    return {
        "key": key,
        "source": _policy_source(loaded, key),
        "prefix": binding.prefix,
        "inherited": binding.prefix != info.name,
    }


def _policy_source(
    loaded: LoadedConfig, location: str, *, source: str | None = None
) -> str:
    source = source or loaded.source_for(location)
    return (
        _config_source(Path(source), root=loaded.root) if source else loaded.path.name
    )


def _config_source(path: Path, *, root: Path) -> str:
    return (
        path.relative_to(root).as_posix()
        if path.is_relative_to(root)
        else path.as_posix()
    )


def _target_info(model: ArchitectureModel, target: Target) -> ModuleInfo | None:
    if target.module is None:
        return None
    if target.planned and target.path is not None:
        return model.planned_info(
            target.module, is_package=PurePosixPath(target.path).name == "__init__.py"
        )
    return model.info(target.module)


def _coverage_brief(
    ctx: AnalysisContext, target: Target, *, info: ModuleInfo | None
) -> dict[str, Any]:
    mode = "strict" if ctx.config.strict_coverage else "configured"
    if target.planned:
        governed = is_governed(info, layers_declared=bool(ctx.model.layers))
        return {
            "mode": mode,
            "status": "planned",
            "policy_status": "governed" if governed else "unclassified",
            "reason": "Planned placement; no source has been analyzed at this path yet.",
        }
    if target.path:
        entry = ctx.coverage.for_path(target.path)
        if entry is not None:
            return {"mode": mode, **entry.to_json()}
    entries = [
        entry
        for entry in ctx.coverage.entries
        if target.feature is None or entry.feature == target.feature
    ]
    return {
        "mode": mode,
        "complete": ctx.coverage.complete
        if target.feature is None
        else all(
            entry.status in ("governed", "facade", "excluded") for entry in entries
        ),
        "files": len(entries),
    }


def _covered_policy(info: ModuleInfo | None, coverage: dict[str, Any]) -> str:
    if coverage.get("status") == "uncovered":
        return "This file is outside the analyzed packages. Select its package and source root, then assign an architecture role before implementing here."
    if coverage.get("status") == "excluded":
        return "This file is explicitly outside the analyzed policy. Review its exclusion reason before using it for new production code."
    if coverage.get("status") == "facade":
        return "Package facade: keep implementation and assembly in classified modules. Named exports retain their providers' restrictions when transitive import checking is enabled."
    if (
        coverage.get("status") == "planned"
        and coverage.get("policy_status") == "unclassified"
    ):
        return "This planned placement has no architecture role. Choose a declared layer or explicitly assign the intended package before implementing here."
    return _boundary_policy(info)


def _violation_in(ctx: AnalysisContext, path: str | None, target: Target) -> bool:
    if path is None:
        return False
    if target.kind == "project":
        return True
    if target.path is not None:
        return path == target.path or path.startswith(f"{target.path}/")
    if target.feature is None:
        return False
    package = ctx.files.packages.get(ctx.model.feature_package_for(target.feature))
    return package is not None and path.startswith(f"{package.path}/")


def _third_party(policy: ThirdPartyPolicy) -> str:
    if policy.default == "deny":
        return f"only {', '.join(policy.allow)}" if policy.allow else "none"
    return f"not {', '.join(policy.deny)}" if policy.deny else "all allowed"


def _boundary_policy(info: ModuleInfo | None) -> str:  # noqa: PLR0911
    if info is None:
        return "Layer and feature boundaries apply as configured; wiring and composition roots have exceptions."
    if info.kind is ModuleKind.COMPOSITION_ROOT:
        return "May import across layers and features; ordinary modules must not import the composition root."
    if info.wiring:
        access = (
            "May import across layer boundaries; must not import features. "
            if info.central or info.kind is ModuleKind.SHARED
            else "May import across layers and features. "
        )
        return (
            access + "Third-party policy still applies to assigned layers. "
            "Only composition roots, other wiring modules and its package facade may import wiring."
        )
    if info.kind is ModuleKind.UNCLASSIFIED:
        return (
            "Layer and feature boundaries are not enforced for this module. "
            "Composition-root/wiring import restrictions (SMT106) and configured cycle checks (SMT104) still apply. "
            "Add its package to the intended layer's modules list, or list it in shared or composition_root."
        )
    if info.central:
        return "The assigned layer's boundaries apply; central modules must not import features."
    if info.kind is ModuleKind.SHARED:
        return "Shared modules must not import features; composition-root/wiring restrictions and cycle checks still apply."
    return "Layer, cross-feature and composition-root/wiring restrictions apply as configured."


def _cross_feature(model: ArchitectureModel) -> str:
    cross = model.config.architecture.cross_feature
    if cross.default == "allow":
        return "allowed"
    pairs = [label.replace(" -> ", " → ") for label in cross.labels()]
    return f"only {', '.join(pairs)}" if pairs else "none"


def _wiring(ctx: AnalysisContext, package: str | None) -> tuple[str, ...]:
    """The wiring patterns that cover modules of ``package`` (all without one)."""
    wiring = ctx.config.architecture.wiring
    if package is None:
        return tuple(wiring)
    modules = [m for m in ctx.files.sources if is_within(m, package)]
    return tuple(p for p in wiring if any(module_matches(p, m) for m in modules))


def _tests(ctx: AnalysisContext, target: Target) -> tuple[str, ...]:
    tests = ctx.config.tests
    if tests.layout != "mirror":
        return ("no layout enforced",)
    source = ctx.files.sources.get(target.module or "")
    if target.module and (
        target.planned or (source is not None and not source.is_package)
    ):
        path = mirror_path(ctx, target.module)
        return (f"mirror at {path}",) if path else ("mirror the source tree",)
    if target.feature is not None:
        package = ctx.model.feature_package_for(target.feature)
        path = mirror_path(ctx, f"{package}.<layer>.<module>")
        if path:
            return (f"mirror at {path}",)
    if target.kind == "package" and target.module:
        path = mirror_path(ctx, f"{target.module}.<module>")
        if path:
            return (f"mirror at {path}",)
    return (f"mirror the source tree ({tests.mirror})",)


def _contract_lines(briefing: Briefing) -> list[str]:
    lines = _rule_lines(briefing)
    lines.extend(
        f"File naming: {key}: {value}"
        for key, value in sorted(briefing.file_naming.items())
    )
    lines.extend(
        f"Canonical export: {symbol} -> {export}"
        for symbol, export in sorted(briefing.canonical_exports.items())
    )
    if briefing.type_dispatch_policy:
        policy = briefing.type_dispatch_policy
        lines.append(
            f"Type dispatch: {policy['style']} from {policy['min_branches']} branches {_source_label(briefing.style['type_dispatch'])}"
        )
    if briefing.test_policy:
        policy = briefing.test_policy
        if (
            policy.get("forbidden_mock_paths")
            or policy.get("max_patches_per_test") is not None
        ):
            lines.append(
                f"Test quality: path/patch policies; mocks forbidden in {policy['forbidden_mock_paths']}; maximum patches {policy['max_patches_per_test']}; excluded {policy['quality_exclude']}"
            )
    if briefing.ordering_policy:
        policy = briefing.ordering_policy
        lines.append(
            f"Definition ordering options {_source_label(briefing.style['ordering'])}; module: {', '.join(policy['module'])}; class: {', '.join(policy['class'])}"
        )
    minimum = briefing.python_target.get("minimum")
    if minimum:
        lines.append(f"Minimum Python: {minimum} ({briefing.python_target['source']})")
    for contract in briefing.contracts:
        if not _describes_package_api(contract):
            continue
        required = ", ".join(contract["required"]) or "none"
        public = contract["public"]
        entry_points = (
            "unrestricted"
            if public is None
            else ", ".join(public) or "none (private package)"
        )
        lines.append(f"Package API {contract['name']} ({contract['package']}):")
        lines.append(f"  Required: {required}")
        lines.append(f"  Public: {entry_points}")
        if contract["target_public"] is not None:
            visibility = "public" if contract["target_public"] else "private"
            lines.append(
                f"  Target: {visibility} to callers outside {contract['package']}"
            )
        missing = sorted(set(contract["missing"] + contract["missing_public"]))
        if missing:
            lines.append(f"  Missing: {', '.join(missing)}")
        if "min_imports_to_bundle" in contract:
            lines.append(
                f"  Bundling hint: from {contract['min_imports_to_bundle']} distinct modules per outside caller"
            )
        if "min_importers_to_export" in contract:
            lines.append(
                f"  Export hint: from {contract['min_importers_to_export']} outside importers per name; count tests: {contract['count_tests']}"
            )
    lines.extend(
        f"Style {name}: {_policy_value(name, entry['value'])} {_source_label(entry)}"
        for name, entry in briefing.style.items()
        if name not in {"imports", "ordering", "type_dispatch"}
    )

    if briefing.import_style:
        style = briefing.import_style
        lines.append(
            f"Import style: {style['style']}, maximum relative level {style['max_relative_level']} {_source_label(briefing.style['imports'])}"
        )
    return lines


def _rule_lines(briefing: Briefing) -> list[str]:
    return [
        f"Rule: {name}: {detail['severity']} {_source_label(detail)}".rstrip()
        for name, detail in briefing.rule_details.items()
        if (detail["severity"] != "off" or detail.get("reason"))
        and (
            briefing.target.kind == "project"
            or (detail["scope"] != "test" and detail["family"] != Category.META.value)
        )
    ]


def _source_label(entry: dict[str, Any]) -> str:
    source = entry.get("source")
    key = entry.get("key")
    reason = entry.get("reason")
    description = entry.get("description")
    origin = (
        f"({source}: {key})" if source else f"({description})" if description else ""
    )
    return f"{origin}; reason: {reason}" if reason else origin


def _describes_package_api(contract: dict[str, Any]) -> bool:
    return bool(
        contract["required"]
        or contract["public"] is not None
        or "min_imports_to_bundle" in contract
        or "min_importers_to_export" in contract
    )


def _policy_value(key: str, value: object) -> str:
    if key in {"module_import_exemptions", "call_argument_kinds"} and isinstance(
        value, list
    ):
        return ", ".join(str(item) for item in value) or "none"
    if key == "errors" and isinstance(value, dict):
        return _error_policy(value)
    # Option lists such as dataclass_options read as the keywords they require.
    if isinstance(value, list):
        return ", ".join(f"{item}=True" for item in value) or "none"
    if isinstance(value, dict):
        pairs = (f"{outside} -> {project}" for outside, project in value.items())
        return ", ".join(pairs) or "none"
    return str(value)


def _error_policy(policy: dict[str, Any]) -> str:
    if policy["base"] is None:
        return "none"
    allowed = ", ".join(policy["allow"])
    return f"raise {policy['base']}" + (f"; allow {allowed}" if allowed else "")


def _header(briefing: Briefing, ctx: AnalysisContext) -> list[str]:
    target = briefing.target
    lines: list[str] = []
    match target.kind:
        case "feature":
            lines.append(f"Feature: {target.feature}   ({briefing.package})")
        case "project":
            roots = ", ".join(ctx.config.project.root_packages)
            lines.append(f"Project: {roots}")
            if briefing.features:
                lines.append(f"Features: {', '.join(briefing.features)}")
        case _:
            planned = " [planned]" if target.planned else ""
            lines.append(f"Module: {target.module}   ({target.path}){planned}")
            lines.append(_placement(briefing))
    if (policy := briefing.workspace_policy) is not None:
        lines.append(f"Member: {policy.member}")
        source = policy.architecture
        label = "root" if policy.member == "." else "member"
        architecture = f"{source} ({label})" if source else "no local layout declared"
        lines.append(f"Architecture: {architecture}")
        lines.append(f"Rules: {' < '.join(policy.rules)}")
    return lines


def _placement(briefing: Briefing) -> str:
    feature = briefing.target.feature
    if briefing.feature_planned:
        feature = f"{feature} (planned)"
    match briefing.module_kind:
        case ModuleKind.SHARED:
            return "Kind: shared (importable by all features, must not import features)"
        case ModuleKind.COMPOSITION_ROOT:
            return "Kind: composition root (wires concrete implementations)"
        case ModuleKind.FEATURE if briefing.central:
            return (
                f"Kind: central module in layer {briefing.layer} "
                "(outside the features, must not import them)"
            )
        case ModuleKind.FEATURE if briefing.layer is not None:
            where = f"feature {feature}, " if feature else ""
            return f"Kind: {where}layer {briefing.layer}"
        case ModuleKind.FEATURE:
            return f"Kind: feature {feature}, outside any layer"
        case _:
            return "Kind: unclassified (outside features, layers, shared and composition root)"


def _layer_lines(briefing: Briefing) -> list[str]:
    name_width = max(len(layer.name) for layer in briefing.layers) + 2
    deps = {
        layer.name: " → " + (", ".join(layer.may_depend_on) or "(nothing)")
        for layer in briefing.layers
    }
    dep_width = max(len(text) for text in deps.values()) + 2
    exempt = briefing.is_wiring or briefing.module_kind is ModuleKind.COMPOSITION_ROOT
    lines = [
        "Layers (project defaults; this module may cross layer boundaries):"
        if exempt
        else "Layers:"
    ]
    for layer in briefing.layers:
        marker = "*" if layer.name == briefing.layer else " "
        row = f" {marker}{layer.name:<{name_width - 2}}{deps[layer.name]}"
        if layer.third_party is not None:
            row = f"{row:<{name_width + dep_width}}third-party: {layer.third_party}"
        lines.append(row.rstrip())
    return lines


def _wrap_tests(parts: tuple[str, ...], width: int = 88) -> list[str]:
    prefix = "Tests: "
    lines: list[str] = []
    current = prefix
    for index, part in enumerate(parts):
        text = part + ("," if index < len(parts) - 1 else "")
        candidate = text if current.strip() in ("", "Tests:") else f" {text}"
        if current != prefix and len(current) + len(candidate) > width:
            lines.append(current)
            current = " " * len(prefix) + text
        else:
            current += text if current == prefix else candidate
    lines.append(current)
    return lines


def _violation_line(briefing: Briefing) -> str:
    if briefing.analysis_error:
        return f"Current violations: unavailable ({briefing.analysis_error})"
    counts = briefing.violations
    parts = [
        f"{count} {label if count == 1 else label + 's'}"
        for label, count in (
            ("error", counts.get("errors", 0)),
            ("warning", counts.get("warnings", 0)),
            ("hint", counts.get("hints", 0)),
        )
        if count
    ]
    if not parts:
        return "Current violations: none"
    return f"Current violations: {', '.join(parts)} ({', '.join(briefing.violation_codes)})"
