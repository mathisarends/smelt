from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import ModuleKind, is_within
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import display_module_path, join, last_segment

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import FileIndex, PackageDir
    from smelt.model import ArchitectureModel


def child_packages(files: FileIndex, parent: str) -> list[PackageDir]:
    prefix = f"{parent}."
    return [
        package
        for name, package in sorted(files.packages.items())
        if name.startswith(prefix) and "." not in name[len(prefix) :]
    ]


def package_location(files: FileIndex, package: PackageDir) -> str:
    init = files.sources.get(package.module)
    return init.path if init is not None else package.path


class UnknownLayer(BaseRule):
    code = "SMT301"
    name = "unknown-layer"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A feature contains a package or module outside the declared layers.",
        rationale=(
            "Code outside the layer grid is invisible to every layer rule: voice/models/ "
            "or voice/helpers.py can import anything and be imported by anything. A "
            "feature's __init__.py is fine, and not every feature needs every layer."
        ),
        bad=(
            "voice/\n  domain/\n  application/\n  models/      # not a layer\n"
            "  helpers.py   # in no layer"
        ),
        good="voice/\n  domain/\n    models.py\n  application/",
        fix=(
            "Move the code into a declared layer, or declare its package as a layer "
            "if the architecture has a distinct responsibility for it."
        ),
        config=("architecture.layers",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        model = ctx.model
        paths = [layer.path.split(".") for layer in model.layers.values()]
        for container in model.grid_containers():
            yield from self._check_children(ctx, container, container, paths)

    def _check_children(
        self,
        ctx: AnalysisContext,
        container: str,
        parent: str,
        paths: list[list[str]],
    ) -> Iterator[Violation]:
        model = ctx.model
        if model.has_features:
            yield from self._loose_modules(ctx, parent)
        for package in child_packages(ctx.files, parent):
            info = model.info(package.module)
            if info is not None and info.kind in (
                ModuleKind.SHARED,
                ModuleKind.COMPOSITION_ROOT,
            ):
                continue
            segment = last_segment(package.module)
            rests = [path[1:] for path in paths if path[0] == segment]
            if any(not rest for rest in rests):
                continue
            if rests:
                yield from self._check_children(ctx, container, package.module, rests)
                continue
            owner = info.feature if info and info.feature else container
            layers = list(model.layers)
            yield self.violation(
                f"{owner} contains package {segment}, which is not a declared layer "
                f"({join(layers)})",
                path=package_location(ctx.files, package),
                source_module=package.module,
                feature=info.feature if info else None,
                expected={"layers": layers},
                hint=(
                    f"Move the modules of {display_module_path(model, package.module, package=True)} "
                    "into a layer, or declare the package under architecture.layers."
                ),
            )

    def _loose_modules(self, ctx: AnalysisContext, parent: str) -> Iterator[Violation]:
        model = ctx.model
        layers = list(model.layers)
        for name, source in sorted(ctx.files.sources.items()):
            if source.is_package or name.rsplit(".", 1)[0] != parent:
                continue
            info = model.info(name)
            if info is None or info.layer is not None:
                continue
            if info.kind in (ModuleKind.SHARED, ModuleKind.COMPOSITION_ROOT):
                continue
            owner = info.feature or parent
            yield self.violation(
                f"{owner} contains module {last_segment(name)}.py, which is in no layer "
                f"({join(layers)})",
                path=source.path,
                source_module=name,
                feature=info.feature,
                expected={"layers": layers},
                hint=(
                    f"Move {display_module_path(model, name)} into a layer of {owner}, "
                    "such as infrastructure for configuration and external services."
                ),
            )


class UnclassifiedModule(BaseRule):
    code = "SMT305"
    name = "unclassified-module"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A module maps to no feature, layer, shared set or composition root.",
        rationale=(
            "Unclassified modules are exempt from layer and feature boundary checks: a domain module "
            "may import them. Composition-root/wiring restrictions and configured cycle checks still apply. Central code such as a "
            "database or storage package belongs to a layer via architecture.modules."
        ),
        bad="gateway/\n  platform/db.py    # neither shared, a layer nor a feature",
        good="# smelt.yaml\narchitecture:\n  modules:\n    gateway.platform: infrastructure",
        fix=(
            "Give the package a layer under architecture.modules, list it in "
            "architecture.shared or composition_root, or move it into a feature."
        ),
        config=(
            "architecture.modules",
            "architecture.shared",
            "architecture.composition_root",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        model = ctx.model
        if not model.layers and not model.has_features:
            return
        for name, source in sorted(ctx.files.sources.items()):
            info = model.info(name)
            if source.is_package or info is None:
                continue
            if info.kind is not ModuleKind.UNCLASSIFIED or info.wiring:
                continue
            package = _top_package(model, name)
            yield self.violation(
                f"{name} is not part of a feature, layer, shared or composition root",
                path=source.path,
                source_module=name,
                hint=(
                    f"Give {package} a layer under architecture.modules "
                    f"(e.g. {package}: infrastructure), or list it in shared or "
                    "composition_root."
                ),
            )


def _top_package(model: ArchitectureModel, module: str) -> str:
    """The package directly below the root package, or the module itself."""
    parts = module.split(".")
    if all(
        info.kind is ModuleKind.UNCLASSIFIED
        for name, info in model.modules.items()
        if is_within(name, parts[0])
    ):
        return parts[0]
    if parts[0] in model.config.project.root_packages and len(parts) > 2:  # noqa: PLR2004
        candidate = ".".join(parts[:2])
        if model.is_package(candidate):
            return candidate
    return module
