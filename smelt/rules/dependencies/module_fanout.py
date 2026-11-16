from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import ModuleKind
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.common import skip_import

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.imports import ImportDetail


class ModuleImportFanout(Rule):
    activation = POLICIES["SMT110"]
    code = "SMT110"
    name = "module-import-fanout"
    category = Category.DEPENDENCIES
    default_severity = Severity.HINT
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary="A module imports more project modules than its policy allows.",
        rationale=(
            "Many independent project dependencies can signal that a module coordinates "
            "too many responsibilities. Unlike SMT108, this review counts distinct "
            "direct target modules across every project root package, without requiring "
            "a contract on each target package. Repeated imports and multiple symbols "
            "from one module count once. Facade re-exports count at the facade path "
            "used by the caller, not at their defining modules. Standard-library and "
            "third-party imports never count; TYPE_CHECKING imports follow the existing "
            "architecture.imports.type_checking policy. Initializers and declared "
            "composition roots are exempt by default. This is a design hint, not proof "
            "of excessive coupling; reducing imports alone does not simplify a design."
        ),
        bad="# app/client.py\nfrom app.users.read import Read\nfrom app.users.write import Write\nfrom app.billing.pay import Pay",
        good="# app/client.py, after deliberate facade review\nfrom app.users import Read, Write\nfrom app.billing import Pay",
        fix=(
            "Split a cohesive responsibility into its own module or depend on deliberate "
            "package facades. Review import cycles and initializer side effects before "
            "adding re-exports. No automatic rewrite is performed. Configure "
            "structure.max_module_imports or a package contract's max_module_imports "
            "to activate this opt-in hint (zero is supported). A rule setting or "
            "--select alone has no effect without a threshold. Closest package limits "
            "override the global limit. structure.module_import_exempt defaults to "
            "[facades, composition_root]; package overrides, including [], are supported."
        ),
        config=(
            "structure.max_module_imports",
            "structure.module_import_exempt",
            "structure.contracts",
            "rules",
            "architecture.composition_root",
            "architecture.imports.type_checking",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            limit = ctx.config.structure.max_module_imports
            contract = ctx.contracts.policy_for(source.module, "max_module_imports")
            if contract is not None:
                limit = contract.spec.max_module_imports
            if limit is None:
                continue
            exempt = ctx.config.structure.module_import_exempt
            exemption_contract = ctx.contracts.policy_for(
                source.module, "module_import_exempt"
            )
            if (
                exemption_contract is not None
                and exemption_contract.spec.module_import_exempt is not None
            ):
                exempt = exemption_contract.spec.module_import_exempt
            info = ctx.model.info(source.module)
            if (source.is_package and "facades" in exempt) or (
                "composition_root" in exempt
                and info is not None
                and info.kind is ModuleKind.COMPOSITION_ROOT
            ):
                continue
            targets: dict[str, ImportDetail] = {}
            for detail in ctx.imports.imports_of(source.module):
                if not detail.external and not skip_import(ctx, detail):
                    targets.setdefault(detail.imported, detail)
            if len(targets) <= limit:
                continue
            first = min(
                targets.values(), key=lambda detail: (detail.line, detail.column)
            )
            grouped: dict[str, list[str]] = defaultdict(list)
            for target in sorted(targets):
                package = (
                    target
                    if target in ctx.files.packages
                    else target.rpartition(".")[0]
                )
                grouped[package].append(target)
            hint = "Split cohesive responsibilities or review deliberate package facades and their import cycles."
            dominant = max(sorted(grouped), key=lambda package: len(grouped[package]))
            if len(grouped[dominant]) * 2 > len(targets):
                hint += f" SMT108 can review per-package fanout into {dominant}."
            yield self.violation(
                f"{source.module} imports {len(targets)} project modules (limit {limit}); review its responsibilities or package facades",
                path=source.path,
                line=first.line,
                column=first.column,
                end_line=first.line,
                end_column=first.end_column,
                source_module=source.module,
                expected={
                    "max_module_imports": limit,
                    "count": len(targets),
                    "modules": sorted(targets),
                    "imports_by_package": dict(sorted(grouped.items())),
                    "import_edges": [
                        f"{source.module} -> {target}" for target in sorted(targets)
                    ],
                    "automatic_rewrite": False,
                },
                hint=hint,
            )
