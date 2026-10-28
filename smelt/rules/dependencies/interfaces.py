from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.exports import resolve_dotted
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import is_within
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import import_violation, join, skip_import

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.imports import ImportDetail


class PrivateModuleImport(BaseRule):
    code = "SMT107"
    name = "private-module-import"
    category = Category.DEPENDENCIES
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS, Index.SYNTAX})
    doc = RuleDoc(
        summary="Code outside a contracted package imports a private implementation module.",
        rationale=(
            "Explicit entry points keep callers coupled to a package's supported "
            "interface. Internal imports and intentional symbol re-exports through "
            "public facades remain valid. Wiring and composition roots still obey "
            "these contracts. Layer and feature policies apply independently."
        ),
        bad="# app/client.py\nfrom app.orders.storage import OrderStore",
        good="# app/client.py\nfrom app.orders.api import place_order",
        fix=(
            "Import through one of the contract's public entry points. Expose a "
            "deliberate API there if needed, or explicitly revise the contract. "
            "Public entries are exact module patterns; add .** only when the whole "
            "subtree is intentionally public."
        ),
        config=("structure.contracts", "architecture.imports.type_checking"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        if not any(contract.public is not None for contract in ctx.contracts.resolved):
            return
        for detail in ctx.imports.all_imports():
            if detail.external or skip_import(ctx, detail):
                continue
            for target in _module_targets(ctx, detail):
                for contract in ctx.contracts.resolved:
                    if (
                        contract.public is None
                        or not is_within(target, contract.package)
                        or is_within(detail.importer, contract.package)
                        or contract.exposes(target)
                    ):
                        continue
                    yield import_violation(
                        self,
                        ctx,
                        replace(detail, imported=target),
                        f'{detail.importer} imports private module {target} of contract "{contract.name}"',
                        expected={
                            "contract": contract.name,
                            "package": contract.package,
                            "public": list(contract.public),
                        },
                        hint=f"Use a public entry point of {contract.package}: {join(contract.public, empty='none; this package is private')}.",
                        edge=f"{detail.importer} -> {contract.package} ({contract.name})",
                    )


def _module_targets(ctx: AnalysisContext, detail: ImportDetail) -> Iterator[str]:
    """Keep symbol re-exports public, but resolve explicit module-valued aliases."""
    seen = {detail.imported}
    yield detail.imported
    for name in detail.names:
        resolved = resolve_dotted(name, ctx.syntax.namespace)
        if resolved is not None and resolved[1] is None and resolved[0] not in seen:
            seen.add(resolved[0])
            yield resolved[0]
