from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.facade_usage import FacadeUsage
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

_IMPORTER_PREVIEW = 4


class FacadeExportGap(BaseRule):
    activation = POLICIES["SMT109"]
    code = "SMT109"
    name = "facade-export-gap"
    category = Category.DEPENDENCIES
    default_severity = Severity.HINT
    requires = frozenset({Index.IMPORTS, Index.SYNTAX})
    doc = RuleDoc(
        summary="A public implementation name has many outside importers but no package export.",
        rationale=(
            "A contract's export_at threshold counts distinct analyzed modules outside "
            "the defining package, aggregating aliases and static re-export chains. "
            "One advisory finding per symbol and facade is anchored at the definition. "
            "Tests count only with count_tests: true; TYPE_CHECKING-only imports never "
            "count. Private names, module imports, namespace packages, uncertain bindings "
            "and recognized dynamic facade exports are skipped. Existing package exports and "
            "explicit __all__ re-exports in other modules are "
            "context, not coverage for this facade. Frequency suggests an API review, "
            "not a requirement to expand the public API. Counts cover analyzed code only."
        ),
        bad=(
            "# app/browser/__init__.py is empty\n"
            "# Three outside modules each use:\n"
            "from app.browser.base import BrowserEvents"
        ),
        good=(
            "# After reviewing ownership and initialization behavior:\n"
            "# app/browser/__init__.py\nfrom .base import BrowserEvents\n"
            "__all__ = ['BrowserEvents']\n"
            "# callers\nfrom app.browser import BrowserEvents"
        ),
        fix=(
            "Review whether the name belongs in the package API or whether callers "
            "should move. The finding lists importers and other public export paths. "
            "A cycle witness means adding the facade dependency closes a runtime "
            "import path; no-cycle-found means only that no return path was found in "
            "the analyzed explicit module graph, not that initialization is safe. "
            "Dynamic imports, implicit package initialization and import order are "
            "not proven. No exports, imports or policy are rewritten automatically."
        ),
        config=("structure.contracts",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        contracts = ctx.contracts.facade_policies("export_at")
        if not contracts:
            return
        usage = FacadeUsage(
            ctx.syntax,
            ctx.imports,
            count_tests=any(c.spec.count_tests for c in contracts),
        )
        for contract in contracts:
            threshold = contract.spec.export_at
            if threshold is None or any(
                not boundary.exposes(contract.package)
                for boundary in ctx.contracts.resolved
                if boundary.package == contract.package
            ):
                continue
            for gap in usage.gaps(
                contract.package, count_tests=contract.spec.count_tests
            ):
                importers = sorted((*gap.source_importers, *gap.test_importers))
                if len(importers) < threshold:
                    continue
                qualified = f"{gap.module}.{gap.name}"
                preview = ", ".join(importers[:_IMPORTER_PREVIEW])
                if len(importers) > _IMPORTER_PREVIEW:
                    preview += f", … (+{len(importers) - _IMPORTER_PREVIEW})"
                cycle = (
                    " -> ".join(gap.cycle)
                    if gap.cycle
                    else "no return path found in the analyzed explicit runtime import graph; initialization safety is unproven"
                )
                repair = f"Review exporting {gap.name} from {contract.package}"
                relative = "." + gap.module.removeprefix(contract.package + ".")
                suggested_import = f"from {relative} import {gap.name}"
                if gap.needs_import:
                    repair += f" with `{suggested_import}`; update __all__ if declared"
                if not gap.needs_import:
                    repair += " by adding it to __all__"
                yield self.violation(
                    f"{qualified} has {len(importers)} outside importers but is not exported by {contract.package}",
                    path=gap.path,
                    line=gap.line,
                    column=gap.column,
                    source_module=gap.module,
                    target_module=contract.package,
                    expected={
                        "contract": contract.name,
                        "package": contract.package,
                        "name": gap.name,
                        "definition": qualified,
                        "export_at": threshold,
                        "count_tests": contract.spec.count_tests,
                        "source_importers": list(gap.source_importers),
                        "test_importers": list(gap.test_importers),
                        "count": len(importers),
                        "reexports": list(gap.reexports),
                        "cycle_check": "cycle" if gap.cycle else "no-cycle-found",
                        "cycle": list(gap.cycle),
                        "needs_import": gap.needs_import,
                        "automatic_rewrite": False,
                        "suggested_import": suggested_import
                        if gap.needs_import
                        else None,
                        "export_edges": [
                            f"{contract.package}:{qualified} <- {caller}"
                            for caller in importers
                        ],
                    },
                    hint=f"{repair}. Importers: {preview}. Cycle check: {cycle}."
                    + (
                        f" Existing exports: {', '.join(gap.reexports)}."
                        if gap.reexports
                        else ""
                    ),
                    edge=f"{qualified} -> {contract.package}",
                )
