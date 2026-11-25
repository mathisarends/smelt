from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.fixes import FixAvailability
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc


class MissingDataclassOption(Rule):
    activation = POLICIES["MOD006"]
    code = "MOD006"
    name = "modeling/missing-dataclass-option"
    category = Category.MODELING
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="A dataclass does not set an option its package requires.",
        rationale=(
            "Whether a dataclass is frozen, uses slots or takes keyword-only "
            "arguments changes how callers may use it, yet codebases drift "
            "between @dataclass, @dataclass(frozen=True) and "
            "@dataclass(frozen=True, slots=True) without anyone being able to "
            "tell which deviations are deliberate. A style override lists "
            "dataclass_options, any of frozen, slots and kw_only, and every "
            "production @dataclass in the package must set each of them to "
            "True. An option that is missing or explicitly False is reported; "
            "a non-literal value is not judged. Missing slots is not reported "
            "when a base class comes from outside the project, such as a "
            "third-party or builtin class without __slots__, because slots "
            "save nothing there; bases from abc and typing still count. It is "
            "also not reported for classes with a functools.cached_property, "
            "which needs an instance __dict__. "
            "Nested packages can set their own list, and [] lifts the "
            "requirement. Tests are not checked."
        ),
        bad=("@dataclass(frozen=True)\nclass Policy:\n    timeout_seconds: float"),
        good=(
            "@dataclass(frozen=True, slots=True)\n"
            "class Policy:\n"
            "    timeout_seconds: float"
        ),
        fix=(
            "Add the missing options to the decorator. A dataclass that must "
            "stay mutable or unslotted keeps a reasoned suppression, or lives "
            "in a package whose contract sets its own dataclass_options. "
            "check --fix --unsafe-fixes sets the missing options to True, unless "
            "the decorator passes **options. Enable it for the project with "
            "style: {dataclass_options: [frozen, slots]}, or for one package "
            "with the same option in a style override."
        ),
        canonical="Set the dataclass options the package requires",
        config=("style.dataclass_options", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        contract = ctx.contracts.policy_for(target.module, "dataclass_options")
        if contract is None or not contract.spec.dataclass_options:
            return None
        options = ", ".join(
            f"{option}=True" for option in contract.spec.dataclass_options
        )
        return f"Declare dataclasses as @dataclass({options})"
