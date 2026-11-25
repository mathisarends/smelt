from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.fixes import FixAvailability
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import GuideTarget, Rule, RuleDoc


class PublicDefinitionsFirst(Rule):
    code = "LAY004"
    name = "layout/definition-order"
    category = Category.LAYOUT
    default_severity = Severity.ERROR
    requires = frozenset({Index.ORDERING})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="Definitions are out of public-first order within modeled execution boundaries.",
        rationale=(
            "Public interfaces should be easy to find. A stable topological order "
            "prioritizes configured categories while preserving category order, "
            "definition-time reads, rebinding and execution barriers. Function bodies "
            "are deferred; decorators, defaults, assignments and eager annotations "
            "are not. Future imports and declared Python 3.14+ targets defer annotations. "
            "Unions of recognized, unshadowed builtin types (and None) are modeled "
            "in eager function annotations for declared Python 3.10+ targets; imported "
            "or user-defined types and operator expressions in defaults remain unknown. "
            "Overload/property families remain atomic. Unknown execution is pinned."
            " Attribute dictionaries of unambiguous undecorated nested functions are distinct from the enclosing ordered namespace, so vars(local_function) and local_function.__dict__ alone do not block enclosing moves. Nonlocal closure writes alone are also unrelated to module/class definition order. Unknown receivers, globals, locals, vars(), global writes and code/annotation introspection still require review."
        ),
        bad="def _load():\n    return 1\n\ndef get():\n    return _load()",
        good="def get():\n    return _load()\n\ndef _load():\n    return 1",
        fix=(
            "Review the expected order and execution barriers. Explicit check --fix "
            "can apply bounded moves where the package permits safe-only fixes. "
            "Imports, unknown execution and a misplaced main guard require manual "
            "review. Names and visibility are never rewritten by ordering."
            " Refused sections identify the triggering expression and its source "
            "location in fix_blocker and fix_reason; the safety boundary stays in place."
            " Pinned definitions are listed under coverage.ordering[].barriers with "
            "check --format json --show-coverage. Dependencies take precedence over "
            "public-first order. On Python 3.12/3.13, keep a needed annotations future "
            "import when the chosen order uses forward references; TYP002 retains it."
        ),
        canonical=(
            "Put public definitions before private ones, in the configured "
            "section order"
        ),
        config=("rules", "style.overrides", "project.python_version"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        policy = ctx.contracts.ordering_brief(target.module or None)
        if policy is None:
            return self.doc.canonical
        return (
            f"Order modules as {', '.join(policy['module'])}; "
            f"classes as {', '.join(policy['class'])}"
        )
