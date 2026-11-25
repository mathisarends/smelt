from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.config.models import POLICY_DEFAULTS
from smelt.diagnostics.fixes import (
    FixAvailability,
)
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc

if TYPE_CHECKING:
    from smelt.config.models import ReexportStyle


class ReexportConvention(Rule):
    activation = POLICIES["VIS008"]
    code = "VIS008"
    name = "visibility/reexport-convention"
    category = Category.VISIBILITY
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="A package initializer spells a re-export against its configured convention.",
        rationale=(
            "Use style.reexports to give package APIs a consistent spelling. "
            "all reports explicit same-name import aliases and uses __all__ for "
            "the public export list. alias reports ordinary imports named by a "
            "statically resolved __all__ when a same-name alias can mark the "
            "re-export. Existing aliases can coexist with an intentional runtime "
            "export list. The rule considers unique, direct import bindings in "
            "production __init__.py files. Real renames, dotted module imports, "
            "guarded or rebound names and star imports are outside its inference. "
            "Selecting the rule without a style uses all."
        ),
        bad=(
            "# style: {reexports: all}\n"
            "# app/events/__init__.py\n"
            "from .models import Event as Event"
        ),
        good='# app/events/__init__.py\nfrom .models import Event\n\n__all__ = ["Event"]',
        fix=(
            "With all, use a plain import and include the name in __all__ if it "
            "belongs to the public API. With alias, use a same-name alias such as "
            "Event as Event and preserve an intentional runtime export list. "
            "For a package that needs either spelling, set reexports to allow "
            "in a style override with a reason. check --fix adds the same-name "
            "alias, or drops it where __all__ already lists the name; adding the "
            "name to __all__, or creating __all__, changes what star imports "
            "export and needs --unsafe-fixes. A dynamic __all__ is left for a "
            "manual edit."
        ),
        canonical="Follow the package's re-export convention in __init__.py",
        config=("rules", "style.reexports", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        style = _reexport_style(ctx, target.module)
        if style == "all":
            return "Re-export in __init__.py with plain imports listed in __all__"
        if style == "alias":
            return "Re-export in __init__.py with same-name aliases (from .x import A as A)"
        return None


def _reexport_style(ctx: AnalysisContext, module: str) -> ReexportStyle:
    contract = ctx.contracts.policy_for(module, "reexports")
    return (contract.spec.reexports if contract else None) or POLICY_DEFAULTS.reexports
