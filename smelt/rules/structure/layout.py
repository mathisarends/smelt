from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import GuideTarget, Rule, RuleDoc


class UnknownLayer(Rule):
    enabled_by_default = True
    code = "ARC011"
    name = "architecture/unknown-layer"
    architecture_scoped = True
    category = Category.ARCHITECTURE
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
            "Move the code into a declared layer, or assign its package to the "
            "intended layer. Use the layer's modules list for packages outside features."
        ),
        canonical="Place feature code inside one of the declared layers",
        config=(
            "architecture.layers",
            "architecture.layers.<layer>.modules",
        ),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        info = target.info
        if info is None:
            return self.doc.canonical if ctx.model.has_features else None
        return self.doc.canonical if info.feature is not None else None


class UnclassifiedModule(Rule):
    code = "ARC012"
    name = "architecture/unclassified-module"
    architecture_scoped = True
    category = Category.ARCHITECTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A module maps to no feature, layer, shared set or composition root.",
        rationale=(
            "Unclassified modules are exempt from layer and feature boundary checks: a domain module "
            "may import them. Composition-root/wiring restrictions and configured cycle checks still apply. Central code such as a "
            "database or storage package can be assigned to a layer's modules list. "
            "Root packages with no classified module at all (libraries) are skipped."
        ),
        bad="gateway/\n  platform/db.py    # neither shared, a layer nor a feature",
        good="# smelt.yml\narchitecture:\n  modules:\n    gateway.platform: infrastructure",
        fix=(
            "Add the package to the intended layer's modules list, list it in "
            "architecture.shared or composition_root, or move it into a feature."
        ),
        canonical=(
            "Place every module in a feature, a layer, the shared set or the "
            "composition root"
        ),
        config=(
            "architecture.layers.<layer>.modules",
            "architecture.shared",
            "architecture.composition_root",
        ),
    )
