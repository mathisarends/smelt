from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc
from smelt.rules.common import readable_list


class LayerBoundary(Rule):
    activation = POLICIES["ARC001"]
    code = "ARC001"
    name = "architecture/layer-boundary"
    architecture_scoped = True
    category = Category.ARCHITECTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary="A layer imports a layer outside its `may_depend_on` allowlist.",
        rationale=(
            "Layers only stay replaceable and testable when dependencies point in one "
            "direction. An application service that imports infrastructure cannot be "
            "used without the database, HTTP client or framework behind it. With "
            "architecture.import_graph.transitive, the allowlist also applies to every "
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
        canonical=(
            "Import only from layers this layer may depend on; reach other "
            "layers through a port wired in the composition root"
        ),
        config=(
            "architecture.layers.<layer>.may_depend_on",
            "architecture.layers.<layer>.modules",
            "architecture.import_graph.transitive",
            "architecture.import_graph.type_checking",
        ),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        layer = target.info.layer if target.info else None
        if layer is None or layer not in ctx.model.layers:
            return self.doc.canonical
        allowed = ctx.model.layers[layer].may_depend_on
        if not allowed:
            return f"From layer {layer}, import no other layer"
        return (
            f"From layer {layer}, import only {readable_list(allowed, conjunction='or')}; "
            "reach other layers through a port"
        )
