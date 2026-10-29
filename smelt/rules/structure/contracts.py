from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import PolicyActivation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class MissingRequiredModule(BaseRule):
    activation = PolicyActivation(
        "A package contract requires modules or named public entry points.",
        lambda config: any(
            contract.required or contract.public
            for contract in config.structure.contracts.values()
        ),
    )
    code = "SMT307"
    name = "missing-required-module"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A package contract is missing a required module or public entry point.",
        rationale=(
            "Explicit package contracts let contributors and agents follow the same "
            "structure. They apply only to matching packages and require no particular "
            "framework, feature layout or layer names. Excluded code does not fulfill "
            "a contract. This checks presence, not implementation quality."
        ),
        bad="structure:\n  contracts:\n    slices:\n      match: app.slices.*\n      required: [api]   # app.slices.orders.api is absent",
        good="structure:\n  contracts:\n    slices:\n      match: app.slices.*\n      required: [api]   # every matching package has an api module or package",
        fix=(
            "Implement the missing module in the matching package. If that package "
            "should follow a different structure, narrow or split the contract. Do "
            "not add empty implementation stubs solely to satisfy the check."
        ),
        config=("structure.contracts",),
        example_language="yaml",
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        index = ctx.contracts
        for contract in index.resolved:
            missing = index.missing(contract)
            absent = [
                *[(module, "required module") for module in missing],
                *[
                    (pattern, "public entry point")
                    for pattern in index.missing_public(contract)
                    if pattern not in missing
                ],
            ]
            info = ctx.model.info(contract.package)
            for module, kind in absent:
                yield self.violation(
                    f'contract "{contract.name}" on {contract.package} is missing {kind} {module}',
                    path=ctx.files.path_for_module(contract.package),
                    source_module=contract.package,
                    target_module=module,
                    feature=info.feature if info else None,
                    layer=info.layer if info else None,
                    expected={
                        "contract": contract.name,
                        "package": contract.package,
                        "module": module,
                        "kind": kind,
                    },
                    hint=(
                        f"Implement {module} under {contract.package}, or narrow "
                        f"structure.contracts.{contract.name} to packages sharing that structure."
                    ),
                )
