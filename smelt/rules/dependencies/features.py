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
        pairs = cross.pairs()
        model = ctx.model
        for detail in ctx.imports.all_imports():
            if detail.external or skip_import(ctx, detail):
                continue
            source = model.info(detail.importer)
            target = model.info(detail.imported)
            if (
                source is None
                or target is None
                or not (source.in_grid and target.in_grid)
            ):
                continue
            if source.feature is None or target.feature is None:
                continue
            if source.feature == target.feature:
                continue
            if source.layer and target.layer and (source.layer, target.layer) in pairs:
                continue
            pair = f"{source.layer or '(feature root)'} -> {target.layer or '(feature root)'}"
            allowed = sorted(cross.allow)
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
        summary="A `shared` module imports a feature module.",
        rationale=(
            "Shared code is importable by every feature. If it depends on a feature, every "
            "feature transitively depends on that feature and the slices collapse."
        ),
        bad="# gateway/shared/money.py\nfrom gateway.features.billing.domain import Currency",
        good="# gateway/shared/money.py\nclass Currency: ...",
        fix="Move the needed concept into shared, or move the shared code into the feature.",
        config=("architecture.shared",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        model = ctx.model
        for detail in ctx.imports.all_imports():
            if detail.external or skip_import(ctx, detail):
                continue
            source = model.info(detail.importer)
            target = model.info(detail.imported)
            if source is None or target is None or source.kind is not ModuleKind.SHARED:
                continue
            if not target.in_grid or target.feature is None:
                continue
            yield import_violation(
                self,
                ctx,
                detail,
                f"shared module {detail.importer} must not import feature {target.feature}",
                expected={"may_depend_on": ["shared", "third-party"]},
                hint=(
                    f"Move what shared needs out of {target.feature} into shared, "
                    f"or move {detail.importer} into the {target.feature} feature."
                ),
            )
