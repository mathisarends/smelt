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


class ModuleDocstringConvention(Rule):
    file_local = True
    activation = POLICIES["SMT309"]
    code = "SMT309"
    name = "layout/module-docstring-convention"
    category = Category.LAYOUT
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A module docstring is missing or present against the package's module_docstrings convention.",
        rationale=(
            "A package chooses one module-docstring convention. require: a "
            "consistent module introduction helps readers understand each file's "
            "responsibility, so every analyzed production module needs a "
            "non-empty initial docstring, including empty files, __init__.py and "
            "__main__.py; comments, later strings, bytes, f-strings, blank "
            "docstrings and class/function docstrings do not satisfy it. forbid: "
            "some projects keep module descriptions in their architecture "
            "documentation, so any initial docstring is reported, including empty "
            "strings. allow explicitly exempts a nested package; an omitted policy "
            "adds no requirement. Separately discovered tests and excluded files "
            "are unaffected."
        ),
        bad="# module_docstrings: require\nVALUE = 1",
        good='"""Constants for the calculation service."""\nVALUE = 1',
        fix="Add a useful module docstring before imports and other statements under require; remove or move the module introduction under forbid. Mark an intentional file exception with # smelt: ignore-file[SMT309] -- reason. No documentation is generated or deleted automatically.",
        config=("structure.contracts",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            contract = ctx.contracts.policy_for(source.module, "module_docstrings")
            if contract is None:
                continue
            mode = contract.spec.module_docstrings
            if mode not in {"forbid", "require"}:
                continue
            tree = ctx.asts.parse(source.path)
            docstring = ast.get_docstring(tree, clean=False)
            expected = {"contract": contract.name, "module_docstrings": mode}
            if mode == "forbid" and docstring is not None:
                node = tree.body[0]
                yield self.violation(
                    f'contract "{contract.name}" forbids module docstrings',
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    end_line=node.end_lineno,
                    end_column=(node.end_col_offset or 0) + 1,
                    source_module=source.module,
                    expected=expected,
                    hint="Remove or relocate the module docstring; keep useful class and function documentation.",
                )
            elif mode == "require" and not (docstring and docstring.strip()):
                yield self.violation(
                    f'contract "{contract.name}" requires a non-empty module docstring',
                    path=source.path,
                    line=1,
                    column=1,
                    source_module=source.module,
                    expected=expected,
                    hint="Add a module docstring before imports, or mark a justified file exception with # smelt: ignore-file[SMT309] -- reason.",
                )


class ForbiddenFutureAnnotations(Rule):
    file_local = True
    activation = POLICIES["SMT310"]
    code = "SMT310"
    name = "typing/forbidden-future-annotations"
    category = Category.TYPING
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


class RedundantFutureAnnotations(Rule):
    file_local = True
    activation = POLICIES["SMT311"]
    code = "SMT311"
    name = "typing/redundant-future-annotations"
    category = Category.TYPING
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
