from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc


class PreferProjectBaseClass(Rule):
    activation = POLICIES["MOD007"]
    code = "MOD007"
    name = "modeling/prefer-project-base-class"
    category = Category.MODELING
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A class inherits an outside base directly instead of the project base.",
        rationale=(
            "Projects often wrap a framework base in their own base class to "
            "share configuration, for example a pydantic BaseModel subclass "
            "that sets extra='forbid' and frozen=True. A class that inherits "
            "the framework base directly silently misses that configuration, "
            "and nothing in the class tells a reviewer. A style override "
            "maps each outside base to the project base, e.g. bases: "
            "{pydantic.BaseModel: app.core.Options}, and production classes in "
            "the package that list the outside base among their direct bases "
            "are reported. Bases are resolved through the module's imports, "
            "including aliases and module attributes. Only inheritance is "
            "judged: importing the outside base for annotations, isinstance "
            "checks or adapters stays valid. The module that defines the "
            "project base is exempt, a nested package can set bases: {} to "
            "allow direct inheritance, and single classes can keep a reasoned "
            "suppression. Tests are not checked."
        ),
        bad=(
            "from pydantic import BaseModel, ConfigDict\n\n"
            "class Manifest(BaseModel):\n"
            '    model_config = ConfigDict(extra="forbid")'
        ),
        good=(
            "from app.core.options import Options\n\nclass Manifest(Options):\n    ..."
        ),
        fix=(
            "Inherit the project base named in the finding and drop "
            "configuration that only repeated it. When a class deliberately "
            "needs the outside base, keep a reasoned suppression or move it "
            "into a package whose contract sets bases: {}. No automatic "
            "rewrite is performed. Enable it for the project with style: "
            "{bases: {pydantic.BaseModel: app.core.Options}}, or for one package "
            "with the same option in a style override."
        ),
        canonical=(
            "Inherit the project base class instead of the configured outside base"
        ),
        config=("style.bases", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        contract = ctx.contracts.policy_for(target.module, "bases")
        if contract is None or not contract.spec.bases:
            return None
        return "; ".join(
            f"Inherit {project} instead of {outside}"
            for outside, project in sorted(contract.spec.bases.items())
        )
