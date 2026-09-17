from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from smelt.config.errors import did_you_mean
from smelt.diagnostics.violation import Severity
from smelt.model import ModuleKind
from smelt.rules.common import implementation_roles, role_home

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext
    from smelt.config.models import ThirdPartyPolicy
    from smelt.diagnostics.report import Report
    from smelt.model import ArchitectureModel


class TargetError(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    kind: str  # project | feature | module | package
    feature: str | None = None
    module: str | None = None
    path: str | None = None


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class RoleBrief:
    name: str
    location: str | None
    note: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "location": self.location, "note": self.note}


@dataclass(frozen=True)
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
    di_frameworks: tuple[str, ...]
    roles: tuple[RoleBrief, ...]
    tests: tuple[str, ...]
    violations: dict[str, int] = field(default_factory=dict)
    violation_codes: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "scope": self.target.kind,
            "feature": self.target.feature,
            "module": self.target.module,
            "path": self.target.path,
            "package": self.package,
            "module_kind": self.module_kind.value if self.module_kind else None,
            "layer": self.layer,
            "features": list(self.features),
            "layers": [layer.to_json() for layer in self.layers],
            "cross_feature": self.cross_feature,
            "shared": list(self.shared),
            "composition_root": list(self.composition_root),
            "di_frameworks": list(self.di_frameworks),
            "roles": [role.to_json() for role in self.roles],
            "tests": list(self.tests),
            "violations": {**self.violations, "codes": list(self.violation_codes)},
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
    candidates = [*model.features, *(p.path for p in files.packages.values())]
    msg = (
        f'"{raw}" is neither a feature nor a source path{did_you_mean(raw, candidates)}'
    )
    raise TargetError(msg)


def build_briefing(ctx: AnalysisContext, target: Target, report: Report) -> Briefing:
    model = ctx.model
    arch = ctx.config.architecture
    info = model.info(target.module) if target.module else None
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
    scoped = [v for v in report.violations if _violation_in(ctx, v.path, target)]
    return Briefing(
        target=target,
        package=package,
        module_kind=info.kind if info else None,
        layer=info.layer if info else None,
        features=tuple(sorted(model.features)),
        layers=layers,
        cross_feature=_cross_feature(model) if model.has_features else None,
        shared=tuple(arch.shared),
        composition_root=tuple(arch.composition_root),
        di_frameworks=tuple(arch.di_frameworks),
        roles=_roles(ctx, target.feature),
        tests=_tests(ctx, target.feature),
        violations={
            "errors": sum(v.severity is Severity.ERROR for v in scoped),
            "warnings": sum(v.severity is Severity.WARNING for v in scoped),
            "hints": sum(v.severity is Severity.HINT for v in scoped),
        },
        violation_codes=tuple(sorted({v.code for v in scoped})),
    )


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


def _third_party(policy: ThirdPartyPolicy) -> str | None:
    if policy.default == "deny":
        return f"only {', '.join(policy.allow)}" if policy.allow else "none"
    return f"not {', '.join(policy.deny)}" if policy.deny else None


def _cross_feature(model: ArchitectureModel) -> str:
    cross = model.config.architecture.cross_feature
    if cross.default == "allow":
        return "allowed"
    pairs = [f"{source} → {target}" for source, target in sorted(cross.pairs())]
    return f"only {', '.join(pairs)}" if pairs else "none"


def _roles(ctx: AnalysisContext, feature: str | None) -> tuple[RoleBrief, ...]:
    model = ctx.model
    placeholder = feature or ("{feature}" if model.has_features else None)
    implementations = set(implementation_roles(model))
    constrained = bool(model.config.architecture.composition_root)
    briefs: list[RoleBrief] = []
    for name, role in model.config.roles.items():
        location = where_path(ctx, name, placeholder) if role.layers else None
        note = None
        if name in implementations and constrained:
            note = "construct only in composition root"
        briefs.append(RoleBrief(name, location, note))
    return tuple(briefs)


def where_path(ctx: AnalysisContext, role: str, feature: str | None) -> str | None:
    home = role_home(ctx.model, role, feature)
    if home is None:
        return None
    module, is_package = home
    return ctx.files.module_to_path(module, package=is_package)


def layer_path(ctx: AnalysisContext, layer: str, feature: str | None) -> str | None:
    package = ctx.model.layer_package(feature, layer)
    if package is None:
        return None
    return ctx.files.module_to_path(package, package=True)


def _tests(ctx: AnalysisContext, feature: str | None) -> tuple[str, ...]:
    tests = ctx.config.tests
    layers = set(ctx.model.layers)
    parts: list[str] = []
    match tests.layout:
        case "feature":
            location = tests.pattern.replace("{feature}", feature or "{feature}")
            parts.append(f"{location.rstrip('/')}/")
        case "mirror":
            parts.append("mirror the source tree")
    if tests.interaction_assertions != "off":
        parts.append("behavior-oriented")
    internal = [c for c in tests.patching.forbid if c in layers]
    if internal:
        parts.append(f"no patching of {'/'.join(internal)} internals")
    if tests.private_access == "forbid":
        parts.append("no private access")
    parts.append(f"≤{tests.mocks.max_per_test} mocks per test")
    if tests.layout != "mirror":
        parts.append("no 1:1 file mirroring required")
    return tuple(parts)


def render_briefing(briefing: Briefing, ctx: AnalysisContext) -> str:
    lines = _header(briefing, ctx)
    lines.append("")
    if briefing.layers:
        lines.extend(_layer_lines(briefing))
    if briefing.cross_feature is not None:
        lines.append(f"Cross-feature:    {briefing.cross_feature}")
    if briefing.shared:
        lines.append(
            f"Shared:           {', '.join(briefing.shared)} (must not import features)"
        )
    if briefing.composition_root:
        di = (
            f" (DI: {', '.join(briefing.di_frameworks)})"
            if briefing.di_frameworks
            else ""
        )
        lines.append(f"Composition root: {', '.join(briefing.composition_root)}{di}")
    placed = [role for role in briefing.roles if role.location]
    if placed:
        width = max(len(role.name) for role in placed) + 2
        lines.extend(("", "Where things go:"))
        for role in placed:
            note = f"   ({role.note})" if role.note else ""
            lines.append(f"  {role.name:<{width}}→ {role.location}{note}")
    lines.extend(("", *_wrap_tests(briefing.tests), "", _violation_line(briefing)))
    return "\n".join(lines) + "\n"


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
            lines.append(f"Module: {target.module}   ({target.path})")
            lines.append(_placement(briefing))
    return lines


def _placement(briefing: Briefing) -> str:
    match briefing.module_kind:
        case ModuleKind.SHARED:
            return "Kind: shared (importable by all features, must not import features)"
        case ModuleKind.COMPOSITION_ROOT:
            return "Kind: composition root (wires concrete implementations)"
        case ModuleKind.FEATURE if briefing.layer is not None:
            feature = briefing.target.feature
            where = f"feature {feature}, " if feature else ""
            return f"Kind: {where}layer {briefing.layer}"
        case ModuleKind.FEATURE:
            return f"Kind: feature {briefing.target.feature}, outside any layer"
        case _:
            return "Kind: unclassified (outside features, layers, shared and composition root)"


def _layer_lines(briefing: Briefing) -> list[str]:
    name_width = max(len(layer.name) for layer in briefing.layers) + 2
    deps = {
        layer.name: " → " + (", ".join(layer.may_depend_on) or "(nothing)")
        for layer in briefing.layers
    }
    dep_width = max(len(text) for text in deps.values()) + 2
    lines = ["Layers:"]
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
