from __future__ import annotations

import itertools
from collections import deque
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, ImportLink, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import (
    display_module_path,
    import_violation,
    join,
    last_segment,
    port_roles,
    role_home,
    skip_import,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.imports import ImportDetail
    from smelt.model import ModuleInfo


class LayerBoundary(BaseRule):
    code = "SMT101"
    name = "layer-boundary"
    category = Category.DEPENDENCIES
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary="A layer imports a layer outside its `may_depend_on` allowlist.",
        rationale=(
            "Layers only stay replaceable and testable when dependencies point in one "
            "direction. An application service that imports infrastructure cannot be "
            "used without the database, HTTP client or framework behind it."
        ),
        bad=(
            "# voice/application/session.py\n"
            "from gateway.features.voice.infra.sql import SqlVoiceSessionRepository"
        ),
        good=(
            "# voice/application/session.py\n"
            "from gateway.features.voice.application.ports import VoiceSessionRepository\n\n"
            "# gateway/bootstrap.py wires SqlVoiceSessionRepository to the port"
        ),
        fix=(
            "Depend on an abstraction (a port) owned by the importing layer or an allowed "
            "layer, and wire the concrete implementation in the composition root."
        ),
        config=(
            "architecture.layers.<layer>.may_depend_on",
            "architecture.imports.transitive",
            "architecture.imports.type_checking",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        model = ctx.model
        for detail in ctx.imports.all_imports():
            if detail.external or skip_import(ctx, detail):
                continue
            source = model.info(detail.importer)
            forbidden = _forbidden_layer(ctx, source, model.info(detail.imported))
            if source is not None and forbidden is not None:
                yield self._violation(ctx, detail, source, forbidden)
        if ctx.config.architecture.imports.transitive:
            yield from self._transitive(ctx)

    def _violation(
        self,
        ctx: AnalysisContext,
        detail: ImportDetail,
        source: ModuleInfo,
        target_layer: str,
        chain: tuple[ImportLink, ...] = (),
    ) -> Violation:
        layer = source.layer or ""
        allowed = list(ctx.model.layers[layer].may_depend_on)
        via = " (indirectly)" if chain else ""
        return import_violation(
            self,
            ctx,
            detail,
            f"{layer} must not depend on {target_layer}{via}",
            expected={"may_depend_on": allowed},
            hint=_hint(ctx, detail, source, chain),
            chain=chain,
        )

    def _transitive(self, ctx: AnalysisContext) -> Iterator[Violation]:
        """Chains that leave the layer grid and come back into a forbidden layer."""
        model = ctx.model
        include_tc = ctx.config.architecture.imports.type_checking == "include"
        adjacency = ctx.imports.first_party_edges(include_type_checking=include_tc)
        for module in sorted(adjacency):
            source = model.info(module)
            if source is None or not source.in_grid or source.layer is None:
                continue
            for target_layer, path in _indirect_targets(
                ctx, module, source, adjacency
            ).items():
                first = next(
                    d
                    for d in ctx.imports.imports_of(module)
                    if d.imported == path[1] and (include_tc or not d.type_checking)
                )
                chain = tuple(
                    ImportLink(a, b, _line(ctx, a, b))
                    for a, b in itertools.pairwise(path)
                )
                yield self._violation(ctx, first, source, target_layer, chain)


def _forbidden_layer(
    ctx: AnalysisContext, source: ModuleInfo | None, target: ModuleInfo | None
) -> str | None:
    """The target's layer if ``source`` must not import ``target``."""
    if (
        source is None
        or target is None
        or source.wiring
        or not (source.in_grid and target.in_grid)
    ):
        return None
    if source.layer is None or target.layer is None:
        return None
    if source.feature != target.feature or source.layer == target.layer:
        return None
    if target.layer in ctx.model.layers[source.layer].may_depend_on:
        return None
    return target.layer


def _indirect_targets(
    ctx: AnalysisContext,
    start: str,
    source: ModuleInfo,
    adjacency: dict[str, set[str]],
) -> dict[str, list[str]]:
    model = ctx.model
    found: dict[str, list[str]] = {}
    previous: dict[str, str] = {}
    queue: deque[str] = deque()
    for child in sorted(adjacency.get(start, ())):
        info = model.info(child)
        if (
            info is not None
            and info.in_grid
            and info.feature == source.feature
            and info.layer
        ):
            continue  # direct edges into the grid are checked directly
        previous[child] = start
        queue.append(child)
    while queue:
        node = queue.popleft()
        for child in sorted(adjacency.get(node, ())):
            if child in previous or child == start:
                continue
            previous[child] = node
            info = model.info(child)
            if (
                info is None
                or not info.in_grid
                or info.feature != source.feature
                or info.layer is None
            ):
                queue.append(child)
                continue
            forbidden = _forbidden_layer(ctx, source, info)
            if forbidden is not None and forbidden not in found:
                path = [child]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                found[forbidden] = list(reversed(path))
    return found


def _line(ctx: AnalysisContext, importer: str, imported: str) -> int | None:
    for detail in ctx.imports.imports_of(importer):
        if detail.imported == imported:
            return detail.line
    return None


def _hint(
    ctx: AnalysisContext,
    detail: ImportDetail,
    source: ModuleInfo,
    chain: tuple[ImportLink, ...],
) -> str:
    model = ctx.model
    name = (
        last_segment(detail.names[0]) if detail.names else last_segment(detail.imported)
    )
    roots = ctx.config.architecture.composition_root
    for role in port_roles(model):
        if source.layer not in model.config.roles[role].layers:
            continue
        home = role_home(model, role, source.feature)
        if home is None:
            continue
        module, is_package = home
        location = display_module_path(model, module, package=is_package)
        wire = f" and wire {name} in {roots[0]}" if roots else ""
        return f"Introduce or reuse a {role} in {location}{wire}."
    layer = source.layer or ""
    allowed = join(list(model.layers[layer].may_depend_on))
    if chain:
        return (
            f"The dependency goes through {join([link.imported for link in chain[:-1]])}; "
            f"break the chain or move the code into a layer {layer} may use ({allowed})."
        )
    return (
        f"Move what {layer} needs into a layer it may depend on ({allowed}), "
        "or invert the dependency behind an abstraction."
    )
