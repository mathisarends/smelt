from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.private_attributes import PrivateAttributeIndex
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class CrossModulePrivateAttribute(Rule):
    code = "SMT365"
    name = "cross-module-private-attribute"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Code accesses a private attribute owned by another module or package.",
        rationale=(
            "Accessing a collaborator's private fields or methods couples the caller "
            "to implementation details. Unlike Ruff SLF001, this opt-in review reports "
            "only accesses whose receiver and defining project class are statically "
            "known. Same-module cooperation is allowed. Reads, writes, calls, updates "
            "and deletes are checked, including self.log._images. Direct accesses on "
            "the method receiver to its own or inherited members are allowed. "
            "Evidence comes from parameter/variable annotations, direct constructor "
            "assignments, stable local aliases, class/instance fields, property return "
            "annotations and bounded single inheritance. Import aliases, relative "
            "imports and static facade re-exports resolve to the defining class. "
            "Dunders, tests, unresolved receivers/members, unknown factories, "
            "inherited lookup across multiple/unknown bases, arbitrary class decorators, local imports, "
            "comprehension/lambda bodies and closure/global-variable flow are skipped. "
            "Rebound parameters and locals are conservatively left unresolved; no "
            "control-flow narrowing, runtime dispatch or analyzed code execution is used."
        ),
        bad=(
            "# app/client.py\nfrom app.session import SessionLog\n"
            "def persist(log: SessionLog):\n    return log._images.retain()"
        ),
        good=(
            "# app/client.py\nfrom app.session import SessionLog\n"
            "def persist(log: SessionLog):\n    return log.retain_images()"
        ),
        fix=(
            "Expose a meaningful operation on the owning class, or pass a deliberate "
            "public dependency to the caller. Do not merely rename or wrap internal "
            "state to silence the report. Preserve evaluation order, exceptions and "
            "side effects. No automatic rewrite is performed. Enable rules: "
            "{cross-module-private-attribute: warning} or select SMT365. "
            "structure.private_attribute_boundary defaults to module; package permits "
            "cooperation between modules of the same immediate Python package. "
            "Closest-package contract overrides and reasoned suppressions are supported. "
            "Boundary options alone do not activate the rule."
        ),
        config=("rules", "structure.private_attribute_boundary", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        index = PrivateAttributeIndex(ctx.syntax, ctx.files)
        for source in ctx.files.sources.values():
            module = ctx.syntax.for_path(source.path)
            if module is None:
                continue
            boundary = ctx.config.structure.private_attribute_boundary
            contract = ctx.contracts.policy_for(
                source.module, "private_attribute_boundary"
            )
            if (
                contract is not None
                and contract.spec.private_attribute_boundary is not None
            ):
                boundary = contract.spec.private_attribute_boundary
            for access in index.accesses(module):
                if access.owner_module == source.module:
                    continue
                owner_source = ctx.files.sources[access.owner_module]
                source_package = (
                    source.module
                    if source.is_package
                    else source.module.rpartition(".")[0]
                )
                owner_package = (
                    access.owner_module
                    if owner_source.is_package
                    else access.owner_module.rpartition(".")[0]
                )
                if boundary == "package" and source_package == owner_package:
                    continue
                target = f"{access.owner_class}.{access.node.attr}"
                yield self.violation(
                    f"{source.module} performs a private {access.operation} on {target}, owned by {access.owner_module}; expose an operation on its owner",
                    path=source.path,
                    line=access.node.lineno,
                    column=access.node.col_offset + 1,
                    end_line=access.node.end_lineno,
                    end_column=(access.node.end_col_offset or 0) + 1,
                    source_module=source.module,
                    target_module=target,
                    expected={
                        "boundary": boundary,
                        "owner_module": access.owner_module,
                        "owner_class": access.owner_class,
                        "receiver_type": access.receiver_type,
                        "attribute": access.node.attr,
                        "operation": access.operation,
                        "automatic_rewrite": False,
                    },
                    hint="Use a deliberate public operation on the collaborator; keep justified cooperation with a narrow suppression or package policy.",
                )
