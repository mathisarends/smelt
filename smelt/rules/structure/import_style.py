from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.fixes import FixAvailability
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc


class ImportStyle(Rule):
    activation = POLICIES["IMP003"]
    code = "IMP003"
    name = "imports/forbidden-import-style"
    category = Category.IMPORTS
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="An import does not follow the package's declared relative/absolute style.",
        rationale=(
            "A predictable import style makes ownership easier to read. With relative "
            "style and max_relative_level: 1, local imports use one dot; parent and "
            "cross-root imports stay absolute. Deeper relative imports can be explicitly "
            "allowed. The closest style override supplies the style, else "
            "style.imports. No style is enforced without one, and this rule "
            "never changes bindings."
        ),
        bad="# app/storage/service.py, relative style with maximum 1\nfrom app.storage.models import Record\nfrom ..domain import Entity",
        good="# app/storage/service.py\nfrom .models import Record\nfrom app.domain import Entity",
        fix=(
            "Use a relative import within the configured dot limit, otherwise use "
            "an absolute import. Preserve aliases and update references if converting "
            "an import statement changes its binding. check --fix rewrites the "
            "module of a from-import, which keeps every binding; a plain import "
            "statement is left for a manual edit."
        ),
        canonical="Follow the package's relative/absolute import style",
        config=(
            "style.imports",
            "style.overrides",
            "architecture.import_graph.type_checking",
        ),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        contract = ctx.contracts.style_for(target.module)
        policy = contract.spec.imports if contract else None
        if policy is None:
            return None
        if policy.style == "absolute":
            return "Use absolute imports for first-party modules"
        dots = (
            "one dot"
            if policy.max_relative_level == 1
            else (f"{policy.max_relative_level} dots")
        )
        return f"Use relative imports up to {dots} inside the package; absolute beyond"
