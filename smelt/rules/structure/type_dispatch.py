from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc


class PreferMatchTypeDispatch(Rule):
    activation = POLICIES["IDM002"]
    code = "IDM002"
    name = "idioms/prefer-match-type-dispatch"
    category = Category.IDIOMS
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Review a same-subject isinstance/elif chain for match/case under an explicit package policy.",
        rationale="Long type-dispatch chains can be clearer as class patterns and guards. This opt-in hint recognizes positive builtin isinstance tests over the same simple name, optionally followed by and guards. It preserves independent adjacent if statements and reports one candidate per whole supported chain. It is a readability suggestion, not proof of equivalent class-pattern behavior.",
        bad="if isinstance(event, Started):\n    start(event)\nelif isinstance(event, Delta | Progress):\n    update(event)\nelif isinstance(event, Finished) and event.ok:\n    finish(event)",
        good="match event:\n    case Started():\n        start(event)\n    case Delta() | Progress():\n        update(event)\n    case Finished() if event.ok:\n        finish(event)",
        fix="Review class patterns, guards and subclass ordering. Preserve independent if statements, and keep nested body conditions inside their case: moving them into a case guard can introduce fallthrough. Match evaluates its subject once; custom instance checks, dynamic type expressions and side effects may differ. No automatic rewrite is performed. Declared Python minima before 3.10 are excluded; unknown minima require version review.",
        canonical=(
            "Dispatch on the type of one subject with match/case instead of "
            "an isinstance chain"
        ),
        config=(
            "style.type_dispatch",
            "style.overrides",
            "project.python_version",
        ),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        contract = ctx.contracts.policy_for(target.module, "type_dispatch")
        policy = contract.spec.type_dispatch if contract else None
        if policy is None or policy.style != "match":
            return None
        return (
            "Dispatch on one subject's type with match/case once an isinstance chain "
            f"reaches {policy.min_branches} branches"
        )
