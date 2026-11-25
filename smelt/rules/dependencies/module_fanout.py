from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc


class ModuleImportFanout(Rule):
    activation = POLICIES["IMP002"]
    code = "IMP002"
    name = "imports/excess-module-imports"
    category = Category.IMPORTS
    default_severity = Severity.HINT
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary="A module imports more project modules than its policy allows.",
        rationale=(
            "Many independent project dependencies can signal that a module coordinates "
            "too many responsibilities. Unlike IMP001, this review counts distinct "
            "direct target modules across every project root package, without requiring "
            "a contract on each target package. Repeated imports and multiple symbols "
            "from one module count once. Facade re-exports count at the facade path "
            "used by the caller, not at their defining modules. Standard-library and "
            "third-party imports never count; TYPE_CHECKING imports follow the existing "
            "architecture.import_graph.type_checking policy. Initializers and declared "
            "composition roots are exempt by default. This is a design hint, not proof "
            "of excessive coupling; reducing imports alone does not simplify a design."
        ),
        bad="# app/client.py\nfrom app.users.read import Read\nfrom app.users.write import Write\nfrom app.billing.pay import Pay",
        good="# app/client.py, after deliberate facade review\nfrom app.users import Read, Write\nfrom app.billing import Pay",
        fix=(
            "Split a cohesive responsibility into its own module or depend on deliberate "
            "package facades. Review import cycles and initializer side effects before "
            "adding re-exports. No automatic rewrite is performed. Enable it with "
            "rules: {imports/excess-module-imports: {severity: hint, max: 15}}; "
            "max defaults to 20, zero is supported, and an entry in a style "
            "override's rules refines it for that package. module_import_exemptions "
            "defaults to [facades, composition_root]; [] checks both."
        ),
        canonical=(
            "Keep project imports per module within the limit; split modules "
            "by responsibility"
        ),
        config=(
            "style.module_import_exemptions",
            "style.overrides",
            "rules",
            "architecture.composition_root",
            "architecture.import_graph.type_checking",
        ),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        limit, _ = ctx.contracts.threshold(target.module, "max_module_imports")
        return (
            f"Import at most {limit} project modules per module; "
            "split by responsibility or import package facades"
        )
