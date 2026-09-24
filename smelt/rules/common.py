from __future__ import annotations

from typing import TYPE_CHECKING, Any

from smelt.model import ArchitectureModel, ModuleInfo, is_within

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext
    from smelt.analysis.imports import ImportDetail
    from smelt.diagnostics.violation import ImportLink, Violation
    from smelt.rules.base import BaseRule


def skip_import(ctx: AnalysisContext, detail: ImportDetail) -> bool:
    return (
        detail.type_checking
        and ctx.config.architecture.imports.type_checking == "ignore"
    )


def wiring_facade(
    model: ArchitectureModel, source: ModuleInfo, target: ModuleInfo
) -> bool:
    """A feature or package initializer may export its own provider module."""
    return target.wiring and (
        source.name == target.name.rsplit(".", 1)[0]
        or (
            target.feature is not None
            and source.feature == target.feature
            and source.name == model.feature_package_for(target.feature)
        )
    )


def import_violation(  # noqa: PLR0913
    rule: BaseRule,
    ctx: AnalysisContext,
    detail: ImportDetail,
    message: str,
    *,
    expected: dict[str, Any] | None = None,
    hint: str | None = None,
    chain: tuple[ImportLink, ...] = (),
) -> Violation:
    source = ctx.files.sources[detail.importer]
    info = ctx.model.info(detail.importer)
    return rule.violation(
        message,
        path=source.path,
        line=detail.line,
        column=detail.column,
        end_line=detail.line,
        end_column=detail.end_column,
        source_module=detail.importer,
        target_module=detail.imported,
        import_chain=chain,
        feature=info.feature if info else None,
        layer=info.layer if info else None,
        expected=expected,
        hint=hint,
    )


def matches_any(name: str, entries: list[str]) -> bool:
    return any(is_within(name, entry) for entry in entries)


def display_module_path(
    model: ArchitectureModel, module: str, *, package: bool = False
) -> str:
    """A short path for hints: relative to the feature container (``voice/application/ports.py``)."""
    spec = model.config.architecture.features
    container: str | None = None
    if spec is not None and spec.root is not None:
        container = spec.root
    elif spec is not None and spec.pattern is not None:
        container = spec.pattern.rsplit(".", 1)[0]
    if container and module.startswith(f"{container}."):
        module = module[len(container) + 1 :]
    elif spec is None:
        root = module.split(".")[0]
        if root in model.config.project.root_packages and "." in module:
            module = module[len(root) + 1 :]
    return module.replace(".", "/") + ("/" if package else ".py")


def role_home(
    model: ArchitectureModel, role: str, feature: str | None
) -> tuple[str, bool] | None:
    """The canonical module (or package, if the role has no ``file``) for a role."""
    config = model.config.roles.get(role)
    if config is None or not config.layers:
        return None
    layer = config.layers[0]
    if model.has_features and feature is None:
        return None
    package = model.layer_package(feature, layer)
    if package is None:
        return None
    if config.module_name:
        return f"{package}.{config.module_name}", False
    return package, True


def port_roles(model: ArchitectureModel) -> list[str]:
    """Roles that other roles implement (e.g. ``port``)."""
    targets = {
        r.detect.implements for r in model.config.roles.values() if r.detect.implements
    }
    return sorted(name for name in model.config.roles if name in targets)


def implementation_roles(model: ArchitectureModel) -> list[str]:
    return sorted(name for name, r in model.config.roles.items() if r.detect.implements)


def join(values: list[str] | tuple[str, ...], empty: str = "(nothing)") -> str:
    return ", ".join(values) if values else empty


def last_segment(name: str) -> str:
    return name.rsplit(".", 1)[-1]
