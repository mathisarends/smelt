from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, ImportLink, Severity, Violation
from smelt.rules.activation import PolicyActivation
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import (
    boundary_imports,
    import_violation,
    join,
    skip_import,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.imports import ImportDetail
    from smelt.model import ModuleInfo


class LayerBoundary(BaseRule):
    activation = PolicyActivation(
        "architecture.layers defines layer dependencies.",
        lambda config: bool(config.architecture.layers),
    )
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
            "used without the database, HTTP client or framework behind it. With "
            "architecture.imports.transitive, the allowlist also applies to every "
            "reachable layer, including paths through allowed layers and helpers. "
            "Indirect findings include a shortest import chain."
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
            "architecture.modules",
            "architecture.imports.transitive",
            "architecture.imports.type_checking",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        model = ctx.model
        for detail, chain in boundary_imports(ctx):
            if detail.external or skip_import(ctx, detail):
                continue
            source = model.info(detail.importer)
            forbidden = _forbidden_layer(ctx, source, model.info(detail.imported))
            if source is not None and forbidden is not None:
                yield self._violation(ctx, detail, source, forbidden, chain)

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
    if source.layer == target.layer:
        return None
    # across features SMT102 decides; a central module shares its layer rules with all
    if source.feature != target.feature and not (source.central or target.central):
        return None
    if target.layer in ctx.model.layers[source.layer].may_depend_on:
        return None
    return target.layer


def _hint(
    ctx: AnalysisContext,
    detail: ImportDetail,
    source: ModuleInfo,
    chain: tuple[ImportLink, ...],
) -> str:
    model = ctx.model
    layer = source.layer or ""
    allowed = join(list(model.layers[layer].may_depend_on))
    if not model.layers[layer].may_depend_on and not chain:
        return (
            f"{layer} depends on no other layer. Define what it needs as an "
            f"abstraction in {layer} and implement it in an outer layer."
        )
    if chain:
        return (
            f"The dependency goes through {join([link.imported for link in chain[:-1]])}; "
            f"break the chain or move the code into a layer {layer} may use ({allowed})."
        )
    return (
        f"Move what {layer} needs into a layer it may depend on ({allowed}), "
        "or invert the dependency behind an abstraction."
    )
