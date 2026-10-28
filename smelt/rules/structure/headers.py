from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.annotations import has_postponement_evidence
from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import PolicyActivation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class ForbiddenModuleDocstring(BaseRule):
    activation = PolicyActivation(
        "A package contract forbids module docstrings.",
        lambda config: any(
            contract.module_docstrings == "forbid"
            for contract in config.structure.contracts.values()
        ),
    )
    code = "SMT309"
    name = "forbidden-module-docstring"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A module docstring is forbidden by the package's explicit convention.",
        rationale="Some projects keep module descriptions in their architecture documentation. This opt-in convention checks the module's initial docstring, not function or class documentation or ordinary strings.",
        bad='"""Redundant module introduction."""\nVALUE = 1',
        good='VALUE = 1\n\ndef calculate():\n    """Explain the calculation contract."""',
        fix="Remove or move the module introduction according to the package's policy. No documentation is deleted automatically.",
        config=("structure.contracts",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            contract = ctx.contracts.policy_for(source.module, "module_docstrings")
            if contract is None or contract.spec.module_docstrings != "forbid":
                continue
            tree = ctx.asts.parse(source.path)
            if ast.get_docstring(tree, clean=False) is not None:
                node = tree.body[0]
                yield self.violation(
                    f'contract "{contract.name}" forbids module docstrings',
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    end_line=node.end_lineno,
                    end_column=(node.end_col_offset or 0) + 1,
                    source_module=source.module,
                    expected={"contract": contract.name, "module_docstrings": "forbid"},
                    hint="Remove or relocate the module docstring; keep useful class and function documentation.",
                )


class ForbiddenFutureAnnotations(BaseRule):
    activation = PolicyActivation(
        "A package contract sets future_annotations to forbid or when-needed.",
        lambda config: any(
            contract.future_annotations in {"forbid", "when-needed"}
            for contract in config.structure.contracts.values()
        ),
    )
    code = "SMT310"
    name = "forbidden-future-annotations"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="The annotations future import violates a package or Python-version policy.",
        rationale="An explicit source convention can forbid this import. In when-needed mode, a declared Python 3.14+ minimum also makes it a violation because annotations are already deferred by default. The future import still changes runtime representation, so removal requires checking annotation consumers and never happens automatically.",
        bad="from __future__ import annotations\n\nVALUE = 1",
        good="VALUE = 1",
        fix="Follow the declared convention while preserving forward references and runtime behavior. When-needed mode gives review hints on earlier or unknown targets and rejects the import on declared Python 3.14+ targets. An explicit allow exception can retain a runtime compatibility contract. No automatic rewrite is performed.",
        config=("structure.contracts",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        yield from _future_findings(self, ctx, forbid=True)


class UnnecessaryFutureAnnotations(BaseRule):
    activation = PolicyActivation(
        "A package contract sets future_annotations to when-needed.",
        lambda config: any(
            contract.future_annotations == "when-needed"
            for contract in config.structure.contracts.values()
        ),
    )
    code = "SMT311"
    name = "unnecessary-future-annotations"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Review an annotations future import with no detected postponement need.",
        rationale="On targets before Python 3.14 or with an unknown minimum, when-needed mode retains imports for quoted and unresolved forward references, TYPE_CHECKING, runtime annotation inspection, decorated definitions and complex annotation expressions. Other cases get a conservative review hint, not proof of safe removal. Declared Python 3.14+ targets are handled by SMT310.",
        bad="from __future__ import annotations\n\ndef count(value: int) -> int:\n    return value",
        good="def count(value: int) -> int:\n    return value",
        fix="Review whether postponement is part of the module's runtime contract, then remove unnecessary boilerplate and verify types and behavior. Keep the import if it serves a real purpose; annotate a justified exception if required.",
        config=("structure.contracts",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        yield from _future_findings(self, ctx, forbid=False)


def _future_findings(
    rule: BaseRule, ctx: AnalysisContext, *, forbid: bool
) -> Iterator[Violation]:
    for source in ctx.files.sources.values():
        contract = ctx.contracts.policy_for(source.module, "future_annotations")
        if contract is None:
            continue
        mode = contract.spec.future_annotations
        if mode not in {"forbid", "when-needed"}:
            continue
        python_target = ctx.python_targets.for_path(source.absolute)
        prohibited = mode == "forbid" or (
            mode == "when-needed" and python_target.deferred_annotations
        )
        if prohibited != forbid:
            continue
        tree = ctx.asts.parse(source.path)
        imports = [
            node
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
            and node.level == 0
            and any(alias.name == "annotations" for alias in node.names)
        ]
        if not imports or (
            not prohibited and has_postponement_evidence(tree, python_target.minimum)
        ):
            continue
        for node in imports:
            yield rule.violation(
                f'contract "{contract.name}" forbids the annotations future import'
                if prohibited
                else "no static postponement need found; review the annotations future import",
                path=source.path,
                line=node.lineno,
                column=node.col_offset + 1,
                end_line=node.end_lineno,
                end_column=(node.end_col_offset or 0) + 1,
                source_module=source.module,
                expected={
                    "contract": contract.name,
                    "future_annotations": mode,
                    "automatic_removal": False,
                    "python": python_target.to_json(),
                },
                hint="Follow the package convention and verify forward references, runtime annotation consumers and tests before removing the import.",
            )
