from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import ModuleKind
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import import_violation, join, skip_import

if TYPE_CHECKING:
    from collections.abc import Iterator


class CrossFeatureImport(BaseRule):
    code = "SMT102"
    name = "cross-feature-import"
    category = Category.DEPENDENCIES
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary="A feature imports another feature outside the allowed layer pairs.",
        rationale=(
            "Features are independent slices. Direct imports between them couple release "
            "cycles and make it impossible to reason about one feature in isolation."
        ),
        bad="# billing/infra/charges.py\nfrom gateway.features.voice.infra.sql import CallLog",
        good=(
            "# billing/application/charges.py\n"
            "from gateway.features.voice.application.api import CallSummaryQuery"
        ),
        fix=(
            "Go through an allowed layer pair (for example application -> application), "
            "move the shared concept into a `shared` package, or merge the features."
        ),
        config=(
            "architecture.cross_feature.default",
            "architecture.cross_feature.allow",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        cross = ctx.config.architecture.cross_feature
        if cross.default == "allow":
            return
        model = ctx.model
        for detail in ctx.imports.all_imports():
            if detail.external or skip_import(ctx, detail):
                continue
            source = model.info(detail.importer)
            target = model.info(detail.imported)
            if (
                source is None
                or target is None
                or source.wiring
                or not (source.in_grid and target.in_grid)
            ):
                continue
            if source.feature is None or target.feature is None:
                continue
            if source.feature == target.feature:
                continue
            if (
                source.layer
                and target.layer
                and cross.allows(
                    source.feature, source.layer, target.feature, target.layer
                )
            ):
                continue
            pair = f"{source.layer or '(feature root)'} -> {target.layer or '(feature root)'}"
            allowed = cross.labels()
            shared = ctx.config.architecture.shared
            where = f" or move the shared concept into {shared[0]}" if shared else ""
            yield import_violation(
                self,
                ctx,
                detail,
                f"feature {source.feature} must not import feature {target.feature} ({pair})",
                expected={"cross_feature_allow": allowed},
                hint=(
                    f"Allowed across features: {join(allowed, 'nothing')}. "
                    f"Use an allowed pair{where}."
                ),
            )


class SharedImportsFeature(BaseRule):
    code = "SMT105"
    name = "shared-imports-feature"
    category = Category.DEPENDENCIES
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary=(
            "A `shared` module or a central module (`architecture.modules`) imports a "
            "feature module."
        ),
        rationale=(
            "Shared code and central layers such as a platform package are importable by "
            "every feature. If they depend on a feature, every feature transitively "
            "depends on that feature and the slices collapse."
        ),
        bad="# gateway/shared/money.py\nfrom gateway.features.billing.domain import Currency",
        good="# gateway/shared/money.py\nclass Currency: ...",
        fix="Move the needed concept into shared, or move the shared code into the feature.",
        config=("architecture.shared", "architecture.modules"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        model = ctx.model
        for detail in ctx.imports.all_imports():
            if detail.external or skip_import(ctx, detail):
                continue
            source = model.info(detail.importer)
            target = model.info(detail.imported)
            if source is None or target is None:
                continue
            if source.kind is not ModuleKind.SHARED and not source.central:
                continue
            if not target.in_grid or target.feature is None:
                continue
            kind = "shared" if source.kind is ModuleKind.SHARED else "central"
            yield import_violation(
                self,
                ctx,
                detail,
                f"{kind} module {detail.importer} must not import feature {target.feature}",
                expected={"may_depend_on": [kind, "third-party"]},
                hint=(
                    f"Move what {detail.importer} needs out of {target.feature} into "
                    f"{kind} code, or move {detail.importer} into the {target.feature} "
                    "feature."
                ),
            )
