from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import is_within
from smelt.rules.activation import POLICIES
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import import_violation, skip_import

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.imports import ImportDetail


class PackageImportFanout(BaseRule):
    activation = POLICIES["SMT108"]
    code = "SMT108"
    name = "package-import-fanout"
    category = Category.DEPENDENCIES
    default_severity = Severity.HINT
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary="One caller imports many implementation modules of the same package.",
        rationale=(
            "Repeated imports of neighboring implementation modules can indicate a "
            "missing package API. This is an opt-in design hint, not proof of poor "
            "architecture. Count distinct target modules per source file and contracted "
            "package, not statements, symbols, or imports across the whole repository."
        ),
        bad="# caller.py\nfrom app.users.application.get_user import GetUser\nfrom app.users.application.update_user import UpdateUser\nfrom app.users.application.delete_user import DeleteUser",
        good="# caller.py, after deliberate facade exports are defined\nfrom app.users.application import DeleteUser, GetUser, UpdateUser",
        fix=(
            "Consider exporting the caller's supported API from the package facade. "
            "Check dependency direction and cycles before changing exports. If these "
            "independent dependencies are intentional, retain them or adjust the "
            "advisory threshold. Declare public: ['.'] to enforce facade-only imports."
        ),
        config=("structure.contracts", "architecture.imports.type_checking"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for contract in ctx.contracts.resolved:
            threshold = contract.spec.bundle_imports_at
            if threshold is None:
                continue
            callers: dict[str, dict[str, ImportDetail]] = defaultdict(dict)
            for detail in ctx.imports.all_imports():
                if (
                    detail.external
                    or skip_import(ctx, detail)
                    or is_within(detail.importer, contract.package)
                    or detail.imported == contract.package
                    or not is_within(detail.imported, contract.package)
                ):
                    continue
                callers[detail.importer].setdefault(detail.imported, detail)
            for caller, targets in sorted(callers.items()):
                if len(targets) < threshold:
                    continue
                first = min(
                    targets.values(), key=lambda detail: (detail.line, detail.column)
                )
                yield import_violation(
                    self,
                    ctx,
                    first,
                    f"{caller} imports {len(targets)} modules of {contract.package}; consider its package facade",
                    expected={
                        "contract": contract.name,
                        "package": contract.package,
                        "bundle_imports_at": threshold,
                        "modules": sorted(targets),
                        "count": len(targets),
                    },
                    hint=f"Consider deliberate exports from {contract.package}, then combine the imports there. Preserve the dependency direction and check for cycles.",
                    edge=f"{caller} -> {contract.package} ({contract.name})",
                )
