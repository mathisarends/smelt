from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.annotations import has_postponement_evidence
from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class ForbiddenModuleDocstring(Rule):
    file_local = True
    activation = POLICIES["SMT309"]
    code = "SMT309"
    name = "forbidden-module-docstring"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A module docstring is forbidden by the package's explicit convention.",
        rationale="Some projects keep module descriptions in their architecture documentation. This opt-in convention checks the module's initial docstring, including empty strings and package initializers, not function or class documentation or ordinary strings. Choose module_docstrings: forbid for this rule or require for the opposite convention (SMT325); allow explicitly exempts a nested package.",
        bad='"""Redundant module introduction."""\nVALUE = 1',
        good='VALUE = 1\n\ndef calculate():\n    """Explain the calculation contract."""',
        fix="Remove or move the module introduction according to the package's policy. Mark an intentional file exception with # smelt: ignore-file[SMT309] -- reason. No documentation is deleted automatically.",
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


class MissingModuleDocstring(Rule):
    file_local = True
    activation = POLICIES["SMT325"]
    code = "SMT325"
    name = "missing-module-docstring"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A module lacks the non-empty docstring required by its package convention.",
        rationale="A consistent module introduction helps readers understand each file's responsibility. With module_docstrings: require, every analyzed production module in the package needs a non-empty initial docstring, including empty files, __init__.py and __main__.py. Comments, later strings, bytes, f-strings and class/function docstrings do not satisfy the policy. Blank docstrings are reported too. Choose forbid for the opposite convention (SMT309); allow explicitly exempts a nested package. Omitted policy adds no requirement. Only analyzed source files are checked; separately discovered tests and excluded files are unaffected.",
        bad="VALUE = 1",
        good='"""Constants for the calculation service."""\nVALUE = 1',
        fix="Add a useful module docstring before imports and other statements. Mark an intentional file exception with # smelt: ignore-file[SMT325] -- reason. No documentation is generated automatically.",
        config=("structure.contracts",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            contract = ctx.contracts.policy_for(source.module, "module_docstrings")
            if contract is None or contract.spec.module_docstrings != "require":
                continue
            tree = ctx.asts.parse(source.path)
            docstring = ast.get_docstring(tree, clean=False)
            if docstring is not None and docstring.strip():
                continue
            yield self.violation(
                f'contract "{contract.name}" requires a non-empty module docstring',
                path=source.path,
                line=1,
                column=1,
                source_module=source.module,
                expected={"contract": contract.name, "module_docstrings": "require"},
                hint="Add a module docstring before imports, or mark a justified file exception with # smelt: ignore-file[SMT325] -- reason.",
            )


class ForbiddenFutureAnnotations(Rule):
    file_local = True
    activation = POLICIES["SMT310"]
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


class UnnecessaryFutureAnnotations(Rule):
    file_local = True
    activation = POLICIES["SMT311"]
    code = "SMT311"
    name = "unnecessary-future-annotations"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Review an annotations future import with no detected postponement need.",
        rationale="On targets before Python 3.14 or with an unknown minimum, when-needed mode retains imports for quoted and unresolved forward references, TYPE_CHECKING, runtime annotation inspection, decorated definitions and complex annotation expressions. This includes dataclasses and Pydantic models: decoration can consume annotations, so modern decorated code may receive no hint even when removal happens to work. Other cases get a conservative review hint, not proof of safe removal. Declared Python 3.14+ targets are handled by SMT310.",
        bad="from __future__ import annotations\n\ndef count(value: int) -> int:\n    return value",
        good="def count(value: int) -> int:\n    return value",
        fix="Review whether postponement is part of the module's runtime contract, then remove unnecessary boilerplate and verify types and behavior. On Python 3.12/3.13, an annotations future import is appropriate when the chosen public-first order uses forward references; SMT313 preserves eager dependencies when the import is absent. Keep the import if it serves a real purpose; annotate a justified exception if required. Self is only an alternative for methods whose return contract follows the receiver's subclass, such as returning self or constructing cls; a matching class-name annotation alone is insufficient. No Self rewrite is suggested automatically.",
        config=("structure.contracts",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        yield from _future_findings(self, ctx, forbid=False)


def _future_findings(
    rule: Rule, ctx: AnalysisContext, *, forbid: bool
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
