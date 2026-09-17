from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.imports import ImportDetail, is_stdlib
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import import_violation, join, matches_any, skip_import

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.config.models import ThirdPartyPolicy


def is_denied(policy: ThirdPartyPolicy, names: tuple[str, ...]) -> bool:
    if policy.default == "allow":
        return any(matches_any(name, policy.deny) for name in names)
    return not all(matches_any(name, policy.allow) for name in names)


class ThirdPartyDenied(BaseRule):
    code = "SMT103"
    name = "third-party-denied"
    category = Category.DEPENDENCIES
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary="A layer imports a third-party package its `third_party` policy does not allow.",
        rationale=(
            "Inner layers should not know about frameworks, drivers or SDKs. Keeping them "
            "out makes the core portable and cheap to test."
        ),
        bad="# voice/domain/session.py\nfrom sqlalchemy.orm import Mapped",
        good="# voice/domain/session.py\nfrom dataclasses import dataclass",
        fix=(
            "Move the code that needs the package into a layer that allows it and expose "
            "it through a port, or extend the layer's `third_party` policy."
        ),
        config=(
            "architecture.layers.<layer>.third_party.default",
            "architecture.layers.<layer>.third_party.allow",
            "architecture.layers.<layer>.third_party.deny",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        model = ctx.model
        for detail in ctx.imports.all_imports():
            if (
                not detail.external
                or is_stdlib(detail.imported)
                or skip_import(ctx, detail)
            ):
                continue
            source = model.info(detail.importer)
            if source is None or not source.in_grid or source.layer is None:
                continue
            policy = model.layers[source.layer].third_party
            names = detail.names or (detail.imported,)
            if not is_denied(policy, names):
                continue
            yield import_violation(
                self,
                ctx,
                detail,
                _message(source.layer, detail, policy),
                expected=_expected(policy),
                hint=_hint(ctx, detail, source.layer),
            )


def _message(layer: str, detail: ImportDetail, policy: ThirdPartyPolicy) -> str:
    package = detail.top_level
    if policy.default == "deny":
        allowed = join(policy.allow, "")
        suffix = (
            f"{layer} allows only: {allowed}"
            if allowed
            else f"{layer} allows no third-party packages"
        )
        return f"{layer} must not import {package} ({suffix})"
    return f"{layer} must not import {package} (denied: {join(policy.deny)})"


def _expected(policy: ThirdPartyPolicy) -> dict[str, object]:
    if policy.default == "deny":
        return {"third_party": {"default": "deny", "allow": list(policy.allow)}}
    return {"third_party": {"default": "allow", "deny": list(policy.deny)}}


def _hint(ctx: AnalysisContext, detail: ImportDetail, layer: str) -> str:
    names = detail.names or (detail.imported,)
    welcoming = [
        name
        for name, config in ctx.model.layers.items()
        if name != layer and not is_denied(config.third_party, names)
    ]
    if welcoming:
        return (
            f"Keep {detail.top_level} in a layer that allows it ({join(welcoming)}) "
            f"and let {layer} depend on an abstraction."
        )
    return (
        f"No layer allows {detail.top_level}; add it to a layer's third_party policy."
    )
