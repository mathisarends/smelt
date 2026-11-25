from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.model import ModuleKind
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc


class CrossFeatureImport(Rule):
    activation = POLICIES["ARC002"]
    code = "ARC002"
    name = "architecture/cross-feature-import"
    architecture_scoped = True
    category = Category.ARCHITECTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary="A feature imports another feature outside the allowed layer pairs.",
        rationale=(
            "Features are independent slices. Direct imports between them couple release "
            "cycles and make it impossible to reason about one feature in isolation. "
            "With architecture.import_graph.transitive, allowed pairs also apply to all "
            "reachable feature modules, with a shortest import chain as evidence."
        ),
        bad="# billing/infra/charges.py\nfrom gateway.features.voice.infra.sql import CallLog",
        good=(
            "# billing/application/charges.py\n"
            "from gateway.features.voice.application.api import CallSummaryQuery"
        ),
        fix=(
            "Go through an allowed relationship, declare an intended one explicitly "
            "(`allow: [{from: orders.application, to: billing.application}]`), move the "
            "shared concept into a `shared` package, or merge the features."
        ),
        canonical=(
            "Import another feature only through an allowed cross-feature relationship"
        ),
        config=(
            "architecture.cross_feature.default",
            "architecture.cross_feature.allow",
            "architecture.import_graph.transitive",
            "architecture.import_graph.type_checking",
        ),
    )


class SharedImportsFeature(Rule):
    activation = POLICIES["ARC005"]
    code = "ARC005"
    name = "architecture/shared-imports-feature"
    architecture_scoped = True
    category = Category.ARCHITECTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary=("A shared or central module imports a feature module."),
        rationale=(
            "Shared code and central layers such as a platform package are importable by "
            "every feature. If they depend on a feature, every feature transitively "
            "depends on that feature and the slices collapse. With "
            "architecture.import_graph.transitive, paths through arbitrary project helpers "
            "and facades are checked and reported with a shortest import chain."
        ),
        bad="# gateway/shared/money.py\nfrom gateway.features.billing.domain import Currency",
        good="# gateway/shared/money.py\nclass Currency: ...",
        fix="Move the needed concept into shared, or move the shared code into the feature.",
        canonical="Keep shared and central modules independent of feature modules",
        config=(
            "architecture.shared",
            "architecture.layers.<layer>.modules",
            "architecture.import_graph.transitive",
            "architecture.import_graph.type_checking",
        ),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        info = target.info
        if info is None:
            return self.doc.canonical if ctx.model.has_features else None
        if info.kind is ModuleKind.SHARED or info.central:
            return "Import no feature module from this shared or central module"
        return None
