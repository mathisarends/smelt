from __future__ import annotations

import itertools
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterator
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.graphs import shortest_cycle, strongly_connected_components
from smelt.diagnostics.violation import Category, ImportLink, Severity, Violation
from smelt.model import ModuleInfo, ModuleKind, is_within
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import import_violation, skip_import, wiring_facade

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

    def type_checking_only(self, a: T, b: T) -> bool:
        """Every import from ``a`` to ``b`` sits under ``if TYPE_CHECKING:``."""
        return all(d.type_checking for d in self.witnesses[(a, b)])


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
            "They also cause import-order bugs at runtime, unless one edge is imported "
            "only under `if TYPE_CHECKING:`; such cycles are marked "
            "`(type checking only)`. Imports that other rules "
            "already forbid (SMT101, SMT102, SMT106) and outbound imports from "
            "declared wiring are left out, so a cycle report means the cycle is built "
            "entirely from ordinary allowed dependencies."
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

        model = ctx.model

        def feature_of(module: str) -> str | None:
            info = model.info(module)
            return info.feature if info else None

        def layer_of(module: str) -> tuple[str | None, str] | None:
            info = model.info(module)
            return (info.feature, info.layer) if info and info.layer else None

        if "features" in scopes:
            yield from self._report(ctx, features, "features", str, feature_of)
        if "layers" in scopes:
            yield from self._report(ctx, layers, "layers", _layer_label, layer_of)
        if "siblings" in scopes:
            for parent in sorted(siblings):
                yield from self._report(
                    ctx,
                    siblings[parent],
                    f"modules in {parent}",
                    _last_segment,
                    partial(_child_of, parent),
                )

    def _report[T: Hashable](
        self,
        ctx: AnalysisContext,
        graph: _Graph[T],
        scope: str,
        label: Callable[[T], str],
        node_of: Callable[[str], T | None],
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
            static = [
                f"{label(a)} -> {label(b)}"
                for a, b in itertools.pairwise(cycle)
                if graph.type_checking_only(a, b)
            ]
            message = f"import cycle between {scope}: {' -> '.join(names)}"
            expected: dict[str, list[str]] = {"cycle": names}
            hint = (
                "Break one edge: extract what both sides need into a separate module, "
                "or invert one dependency behind an abstraction."
            )
            if static:
                message += " (type checking only)"
                expected["type_checking_only"] = static
                verb = "is" if len(static) == 1 else "are"
                hint = (
                    f"Only type checkers see this cycle: {', '.join(static)} {verb} "
                    "imported under `if TYPE_CHECKING:`, so it cannot fail at import "
                    "time. It still couples both sides. " + hint + " To leave such "
                    "edges out, set architecture.imports.type_checking: ignore."
                )
            yield import_violation(
                self,
                ctx,
                hops[0],
                message,
                expected=expected,
                hint=hint,
                edge=" -> ".join(names),
                chain=_witness(ctx, hops, node_of),
            )


# A witness hop inside one node is short; a longer path explains little.
_MAX_INNER_HOPS = 6


def _witness[T: Hashable](
    ctx: AnalysisContext,
    hops: list[ImportDetail],
    node_of: Callable[[str], T | None],
) -> tuple[ImportLink, ...]:
    """The cycle's imports, joined inside each node where a module path exists.

    ``features -> platform -> features`` crosses into ``platform.auth`` and leaves
    from ``platform.auth.guard``; the witness adds ``platform.auth -> ...guard``.
    Where no path inside the node joins two hops, the gap stays visible.
    """
    include = ctx.config.architecture.imports.type_checking == "include"
    adjacency = ctx.imports.first_party_edges(include_type_checking=include)
    links: list[ImportLink] = []
    for hop, following in itertools.zip_longest(hops, hops[1:]):
        links.append(ImportLink(hop.importer, hop.imported, hop.line))
        if following is None or following.importer == hop.imported:
            continue
        path = _path_within(adjacency, hop.imported, following.importer, node_of)
        links.extend(
            ImportLink(a, b, _line(ctx, a, b)) for a, b in itertools.pairwise(path)
        )
    return tuple(links)


def _path_within[T: Hashable](
    adjacency: dict[str, set[str]],
    start: str,
    goal: str,
    node_of: Callable[[str], T | None],
) -> list[str]:
    node = node_of(start)
    previous: dict[str, str | None] = {start: None}
    frontier = [start]
    for _ in range(_MAX_INNER_HOPS):
        following: list[str] = []
        for module in frontier:
            for child in sorted(adjacency.get(module, ())):
                if child in previous or node_of(child) != node:
                    continue
                previous[child] = module
                if child == goal:
                    path = [goal]
                    while (parent := previous[path[-1]]) is not None:
                        path.append(parent)
                    return list(reversed(path))
                following.append(child)
        frontier = following
    return []


def _line(ctx: AnalysisContext, importer: str, imported: str) -> int | None:
    return next(
        (d.line for d in ctx.imports.imports_of(importer) if d.imported == imported),
        None,
    )


def _child_of(parent: str, module: str) -> str | None:
    """The child of ``parent`` containing ``module``: the node of a sibling cycle."""
    if not is_within(module, parent) or module == parent:
        return None
    return ".".join(module.split(".")[: parent.count(".") + 2])


def _reported_elsewhere(
    ctx: AnalysisContext, source: ModuleInfo, target: ModuleInfo
) -> bool:
    """Ignore wiring edges and imports already reported by boundary rules."""
    if source.wiring:
        return True
    if (
        (target.kind is ModuleKind.COMPOSITION_ROOT or target.wiring)
        and source.kind is not ModuleKind.COMPOSITION_ROOT
        and not source.wiring
        and not wiring_facade(ctx.model, source, target)
    ):
        return True
    if not (source.in_grid and target.in_grid and source.layer and target.layer):
        return False
    if source.feature == target.feature:
        allowed = ctx.model.layers[source.layer].may_depend_on
        return source.layer != target.layer and target.layer not in allowed
    cross = ctx.config.architecture.cross_feature
    return (
        cross.default == "deny"
        and source.feature is not None
        and target.feature is not None
        and not cross.allows(source.feature, source.layer, target.feature, target.layer)
    )


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
