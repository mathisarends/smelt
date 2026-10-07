from __future__ import annotations

from typing import TYPE_CHECKING, Any

from smelt.model import ArchitectureModel, ModuleInfo, ModuleKind, is_within

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
    edge: str | None = None,
) -> Violation:
    source = ctx.files.sources[detail.importer]
    info = ctx.model.info(detail.importer)
    if edge is None:
        target = (
            detail.imported.split(".")[0]
            if detail.external
            else architecture_node(ctx, detail.imported)
        )
        edge = f"{architecture_node(ctx, detail.importer)} -> {target}"
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
        edge=edge,
    )


def architecture_node(ctx: AnalysisContext, module: str) -> str:
    """The part of the architecture ``module`` belongs to: ``user.domain``,
    a central package, a shared or composition root entry, else the module."""
    info = ctx.model.info(module)
    if info is None:
        return module
    if info.in_grid and (info.feature or info.layer):
        return ".".join(part for part in (info.feature, info.layer) if part)
    architecture = ctx.config.architecture
    entries = {
        ModuleKind.SHARED: architecture.shared,
        ModuleKind.COMPOSITION_ROOT: architecture.composition_root,
    }.get(info.kind, list(architecture.modules) if info.central else [])
    matching = [entry for entry in entries if is_within(module, entry)]
    return max(matching, key=len) if matching else module


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


def join(values: list[str] | tuple[str, ...], empty: str = "(nothing)") -> str:
    return ", ".join(values) if values else empty


def last_segment(name: str) -> str:
    return name.rsplit(".", 1)[-1]
