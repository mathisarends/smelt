import itertools
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.graphs import shortest_cycle, strongly_connected_components
from smelt.diagnostics.violation import Category, ImportLink, Severity, Violation
from smelt.model import ModuleInfo, ModuleKind, is_within
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import import_violation, skip_import

if TYPE_CHECKING:
    from smelt.analysis.imports import ImportDetail


@dataclass
class _Graph[T: Hashable]:
    adjacency: dict[T, set[T]] = field(default_factory=lambda: defaultdict(set))
    witnesses: dict[tuple[T, T], list[ImportDetail]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def add(self, a: T, b: T, detail: ImportDetail) -> None:
        if a == b:
            return
        self.adjacency[a].add(b)
        self.witnesses[(a, b)].append(detail)

    def chain(self, cycle: list[T]) -> list[ImportDetail]:
        """One import per hop, preferring imports that connect module to module."""
        hops: list[ImportDetail] = []
        for a, b in itertools.pairwise(cycle):
            candidates = self.witnesses[(a, b)]
            previous = hops[-1].imported if hops else None
            connected = [d for d in candidates if d.importer == previous]
            hops.append((connected or candidates)[0])
        return hops


class ImportCycle(BaseRule):
    code = "SMT104"
    name = "import-cycle"
    category = Category.DEPENDENCIES
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary="Import cycles between features, between layers, or between sibling modules.",
        rationale=(
            "Cycles mean two parts can only be understood, tested and changed together. "
            "They also cause import-order bugs at runtime. Imports that other rules "
            "already forbid (SMT101, SMT102, SMT106) are left out, so a cycle report "
            "means the cycle is built entirely from allowed dependencies."
        ),
        bad=(
            "# billing/application/invoices.py\n"
            "from gateway.features.voice.application import calls\n"
            "# voice/application/calls.py\n"
            "from gateway.features.billing.application import invoices"
        ),
        good=(
            "# voice/application/calls.py publishes an event;\n"
            "# billing/application/invoices.py subscribes to it."
        ),
        fix=(
            "Break one edge of the cycle: extract the shared piece into a module both can "
            "import, invert one dependency behind an abstraction, or merge the parts."
        ),
        config=("architecture.imports.cycles", "architecture.imports.type_checking"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        scopes: set[str] = set(ctx.config.architecture.imports.cycles)
        features: _Graph[str] = _Graph()
        layers: _Graph[tuple[str | None, str]] = _Graph()
        siblings: dict[str, _Graph[str]] = defaultdict(_Graph)
        skip_packages = _packages_covered_by_other_scopes(ctx, scopes)

        for detail in ctx.imports.all_imports():
            if detail.external or skip_import(ctx, detail):
                continue
            source = ctx.model.info(detail.importer)
            target = ctx.model.info(detail.imported)
            if (
                source is None
                or target is None
                or _reported_elsewhere(ctx, source, target)
            ):
                continue
            if source.in_grid and target.in_grid:
                if source.feature and target.feature:
                    features.add(source.feature, target.feature, detail)
                if source.feature == target.feature and source.layer and target.layer:
                    layers.add(
                        (source.feature, source.layer),
                        (target.feature, target.layer),
                        detail,
                    )
            parent, a, b = _sibling_pair(detail.importer, detail.imported)
            if parent is not None and parent not in skip_packages:
                siblings[parent].add(a, b, detail)

        if "features" in scopes:
            yield from self._report(ctx, features, "features", str)
        if "layers" in scopes:
            yield from self._report(ctx, layers, "layers", _layer_label)
        if "siblings" in scopes:
            for parent in sorted(siblings):
                yield from self._report(
                    ctx, siblings[parent], f"modules in {parent}", _last_segment
                )

    def _report[T: Hashable](
        self,
        ctx: AnalysisContext,
        graph: _Graph[T],
        scope: str,
        label: Callable[[T], str],
    ) -> Iterator[Violation]:
        for component in strongly_connected_components(graph.adjacency):
            if len(component) < 2:  # noqa: PLR2004
                continue
            start = min(component, key=str)
            cycle = shortest_cycle(start, graph.adjacency, component)
            if cycle is None:
                continue
            hops = graph.chain(cycle)
            names = [label(node) for node in cycle]
            yield import_violation(
                self,
                ctx,
                hops[0],
                f"import cycle between {scope}: {' -> '.join(names)}",
                expected={"cycle": names},
                hint=(
                    "Break one edge: extract what both sides need into a separate module, "
                    "or invert one dependency behind an abstraction."
                ),
                chain=tuple(ImportLink(d.importer, d.imported, d.line) for d in hops),
            )


def _reported_elsewhere(
    ctx: AnalysisContext, source: ModuleInfo, target: ModuleInfo
) -> bool:
    """Imports that SMT101, SMT102 or SMT106 already report as violations."""
    if (
        target.kind is ModuleKind.COMPOSITION_ROOT
        and source.kind is not ModuleKind.COMPOSITION_ROOT
    ):
        return True
    if not (source.in_grid and target.in_grid and source.layer and target.layer):
        return False
    if source.feature == target.feature:
        allowed = ctx.model.layers[source.layer].may_depend_on
        return source.layer != target.layer and target.layer not in allowed
    cross = ctx.config.architecture.cross_feature
    return cross.default == "deny" and (source.layer, target.layer) not in cross.pairs()


def _packages_covered_by_other_scopes(
    ctx: AnalysisContext, scopes: set[str]
) -> set[str]:
    model = ctx.model
    covered: set[str] = set()
    if "features" in scopes and model.has_features:
        covered.update(
            info.package.rsplit(".", 1)[0] for info in model.features.values()
        )
    if "layers" in scopes and model.layers:
        covered.update(model.grid_containers())
    return covered


def _layer_label(node: tuple[str | None, str]) -> str:
    feature, layer = node
    return f"{feature}.{layer}" if feature else layer


def _last_segment(module: str) -> str:
    return module.rsplit(".", 1)[-1]


def _sibling_pair(importer: str, imported: str) -> tuple[str | None, str, str]:
    if is_within(importer, imported) or is_within(imported, importer):
        return None, "", ""
    a, b = importer.split("."), imported.split(".")
    common = 0
    while common < min(len(a), len(b)) and a[common] == b[common]:
        common += 1
    if common == 0:
        return None, "", ""
    parent = ".".join(a[:common])
    return parent, ".".join(a[: common + 1]), ".".join(b[: common + 1])
