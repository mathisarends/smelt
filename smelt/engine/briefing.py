from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from smelt.config.errors import did_you_mean
from smelt.config.patterns import module_matches
from smelt.diagnostics.violation import Severity
from smelt.model import ModuleKind, is_within
from smelt.rules.testing.location import mirror_path

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
            "wiring": list(self.wiring),
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
    if model.features:
        msg += f"; features: {', '.join(sorted(model.features))}"
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
        wiring=_wiring(ctx, package),
        central=bool(info and info.central),
        tests=_tests(ctx, target),
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
    if source is not None and not source.is_package:
        path = mirror_path(ctx, source.module)
        return (f"mirror at {path}",) if path else ("mirror the source tree",)
    if target.feature is not None:
        package = ctx.model.feature_package_for(target.feature)
        path = mirror_path(ctx, f"{package}.<layer>.<module>")
        if path:
            return (f"mirror at {path}",)
    return (f"mirror the source tree ({tests.mirror})",)


def render_briefing(briefing: Briefing, ctx: AnalysisContext) -> str:
    lines = _header(briefing, ctx)
    lines.append("")
    if briefing.module_kind is ModuleKind.UNCLASSIFIED:
        lines.extend(
            (
                "No boundary rule covers this module: it may import anything and be",
                "imported from any layer. Give its package a layer under",
                "architecture.modules, or list it in shared or composition_root.",
                "",
                _violation_line(briefing),
            )
        )
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
        case ModuleKind.FEATURE if briefing.central:
            return (
                f"Kind: central module in layer {briefing.layer} "
                "(outside the features, must not import them)"
            )
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
