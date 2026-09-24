from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import ModuleKind
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import display_module_path, join, last_segment

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import FileIndex, PackageDir


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
            "Move the modules into a layer, declare the package under "
            "architecture.layers, or add it to architecture.shared."
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
                    "into a layer, declare it under architecture.layers, or add it to "
                    "architecture.shared."
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
                    "or add it to architecture.shared."
                ),
            )


class CrowdedPackage(BaseRule):
    code = "SMT304"
    name = "crowded-package"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A package has more modules than structure.crowded_threshold.",
        rationale=(
            "A flat package with many modules hides which of them belong together. "
            "This is advisory: grouping is a judgement call."
        ),
        bad="voice/application/\n  start.py\n  stop.py\n  ... 13 modules",
        good="voice/application/\n  sessions/\n  recordings/",
        fix="Group related modules into subpackages, or raise the threshold.",
        config=("structure.crowded_threshold",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        files = ctx.files
        threshold = ctx.config.structure.crowded_threshold
        counts: dict[str, int] = {}
        for source in files.sources.values():
            if source.is_package or "." not in source.module:
                continue
            parent = source.module.rsplit(".", 1)[0]
            counts[parent] = counts.get(parent, 0) + 1
        for parent, count in sorted(counts.items()):
            package = files.packages.get(parent)
            if count <= threshold or package is None:
                continue
            info = ctx.model.info(parent)
            shown = display_module_path(ctx.model, parent, package=True).rstrip("/")
            yield self.violation(
                f"{shown} has {count} modules; consider grouping related modules",
                path=package_location(files, package),
                source_module=parent,
                feature=info.feature if info else None,
                layer=info.layer if info else None,
                expected={"crowded_threshold": threshold},
            )


class UnclassifiedModule(BaseRule):
    code = "SMT305"
    name = "unclassified-module"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A module maps to no feature, layer, shared set or composition root.",
        rationale=(
            "Unclassified modules are exempt from most rules. A few are normal (entry "
            "points, settings); many mean the config no longer describes the code."
        ),
        bad="gateway/\n  misc.py      # neither shared nor a feature",
        good="gateway/\n  shared/misc.py",
        fix="Move the module into a feature or shared, or list it in architecture.shared.",
        config=("architecture.shared", "architecture.composition_root"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        model = ctx.model
        if not model.layers and not model.has_features:
            return
        for name, source in sorted(ctx.files.sources.items()):
            info = model.info(name)
            if source.is_package or info is None:
                continue
            if info.kind is not ModuleKind.UNCLASSIFIED:
                continue
            yield self.violation(
                f"{name} is not part of a feature, layer, shared or composition root",
                path=source.path,
                source_module=name,
            )
