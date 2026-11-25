from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import GuideTarget, Rule, RuleDoc


class ExcessPrivateDefinitions(Rule):
    code = "VIS007"
    name = "visibility/excess-private-definitions"
    category = Category.VISIBILITY
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A module contains more private definitions than the configured limit.",
        rationale=(
            "A growing collection of private models and helpers can make a module's "
            "responsibility harder to understand. This opt-in review counts private "
            "classes, sync/async functions and PEP 695 type aliases directly in a "
            "production module body. A leading underscore marks privacy; dunders "
            "are excluded. Repeated declarations of the same name and kind, including "
            "overloads, count once at their first declaration. Private uppercase "
            "assignments with values are listed separately as constants and never "
            "contribute to the limit. Imports, ordinary assignment-style type aliases, "
            "methods, nested/local definitions and definitions inside control-flow "
            "statements are excluded. Package initializers are checked too. A few "
            "models beside their consumer remain valid at the default limit of 12. "
            "Even a larger cohesive model tree or parser may be intentional: this "
            "metric does not infer cohesion, separable groups or safe extraction."
        ),
        bad=(
            "# With rules: {visibility/excess-private-definitions: {severity: hint, max: 2}}\n"
            "class _Cookie: pass\n"
            "class _StorageItem: pass\n"
            "def _merge(existing, incoming): ...\n"
            "class AuthenticationState: ..."
        ),
        good=(
            "# A small local model stays beside its consumer.\n"
            "class _Cookie: pass\n"
            "class AuthenticationState: ..."
        ),
        fix=(
            "Review the listed definitions for a cohesive group that deserves its "
            "own module, carrying its constants and validators with it. Choose a "
            "name describing that responsibility; models.py is only one possibility. "
            "Preserve import direction, initialization, framework registration and "
            "module visibility when moving code. Do not make helpers public or "
            "extract arbitrary fragments merely to lower the count. Keep deliberate "
            "cohesive implementations with a reasoned suppression or package override. "
            "No automatic rewrite is performed. Enable rules: "
            "{visibility/excess-private-definitions: hint} or select VIS007; "
            "set max on the entry, in the root or a style override's rules. "
            "The limit defaults to 12; zero is supported."
        ),
        canonical=(
            "Keep private definitions per module within the limit; split "
            "cohesive groups out"
        ),
        config=("rules", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        limit, _ = ctx.contracts.threshold(target.module, "max_private_definitions")
        return (
            f"Keep at most {limit} private definitions per module; "
            "move cohesive groups into their own module"
        )
