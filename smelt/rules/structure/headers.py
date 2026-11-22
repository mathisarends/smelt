from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.annotations import has_postponement_evidence
from smelt.analysis.bindings import (
    binding_counts,
    qualified_name,
    unshadowed_bindings,
)
from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import tree_nodes
from smelt.diagnostics.fixes import Applicability, Fix, FixAvailability
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc
from smelt.rules.edits import SourceText, is_docstring

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from smelt.analysis.contracts import ResolvedContract
    from smelt.analysis.python_version import PythonTarget


class MisplacedModuleLogger(Rule):
    file_local = True
    code = "LAY011"
    name = "layout/misplaced-module-logger"
    category = Category.LAYOUT
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="A module logger is declared outside the block immediately after imports.",
        rationale=(
            "Keeping module loggers together after imports makes logging easy to find "
            "before constants and other module declarations. This opt-in convention "
            "checks direct module-level assignments of logging.getLogger(__name__) "
            "in analyzed source files, including annotated assignments and unshadowed "
            "import aliases. The import section can include future imports, import-only "
            "if blocks and optional imports in try blocks. Comments and blank lines "
            "do not affect placement. Other logger names passed to getLogger, custom "
            "factories, guarded or local assignments and uncertain bindings are outside scope."
        ),
        bad="import logging\n\n_TIMEOUT_SECONDS = 5\n_logger = logging.getLogger(__name__)",
        good="import logging\n\n_logger = logging.getLogger(__name__)\n_TIMEOUT_SECONDS = 5",
        fix=(
            "Place the module logger immediately after the import section and before "
            "constants and other code. Review initialization dependencies before moving "
            "it, and use a reasoned suppression for intentional exceptions. "
            "Enable rules: {layout/misplaced-module-logger: warning} to check the convention. "
            "check --fix moves a logger that stands on its own lines and is bound "
            "once; an annotation must only read imports."
        ),
        canonical="Declare the module logger directly after the imports",
        config=("rules", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            text: SourceText | None = None
            for statement, target in _misplaced_loggers(tree):
                text = text or SourceText(ctx.files.read_text(source.path))
                yield self.violation(
                    f"place module logger {target.id} immediately after imports",
                    path=source.path,
                    line=target.lineno,
                    column=target.col_offset + 1,
                    end_line=target.end_lineno,
                    end_column=(target.end_col_offset or 0) + 1,
                    source_module=source.module,
                    expected={"logger": target.id, "placement": "after-imports"},
                    hint="Group module loggers after imports and before constants. Review initialization dependencies before moving them.",
                    fix=_logger_fix(text, tree, statement, name=target.id),
                )


class ModuleDocstringConvention(Rule):
    file_local = True
    activation = POLICIES["LAY002"]
    code = "LAY002"
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
        fix="Add a useful module docstring before imports and other statements under require; remove or move the module introduction under forbid. Mark an intentional file exception with # smelt: ignore-file[LAY002] -- reason. No documentation is generated or deleted automatically.",
        canonical="Follow the package's module docstring convention",
        config=("style.module_docstrings", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        contract = ctx.contracts.policy_for(target.module, "module_docstrings")
        mode = contract.spec.module_docstrings if contract else None
        if mode == "require":
            return "Start every module with a docstring that states its purpose"
        if mode == "forbid":
            return "Do not write module docstrings"
        return None

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
                    f"{contract.origin} forbids module docstrings",
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
                    f"{contract.origin} requires a non-empty module docstring",
                    path=source.path,
                    line=1,
                    column=1,
                    source_module=source.module,
                    expected=expected,
                    hint="Add a module docstring before imports, or mark a justified file exception with # smelt: ignore-file[LAY002] -- reason.",
                )


class ForbiddenFutureAnnotations(Rule):
    file_local = True
    activation = POLICIES["TYP001"]
    code = "TYP001"
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
        canonical="Follow the package's from __future__ import annotations policy",
        config=("style.future_annotations", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        policy = _future_policy(ctx, target.module, target.absolute(ctx.root))
        if policy is None or not policy.prohibited:
            return None
        return "Never write from __future__ import annotations"

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        yield from _future_findings(self, ctx, forbid=True)


class RedundantFutureAnnotations(Rule):
    file_local = True
    activation = POLICIES["TYP002"]
    code = "TYP002"
    name = "typing/redundant-future-annotations"
    category = Category.TYPING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Review an annotations future import with no detected postponement need.",
        rationale="On targets before Python 3.14 or with an unknown minimum, when-needed mode retains imports for quoted and unresolved forward references, TYPE_CHECKING, runtime annotation inspection, decorated definitions and complex annotation expressions. This includes dataclasses and Pydantic models: decoration can consume annotations, so modern decorated code may receive no hint even when removal happens to work. Other cases get a conservative review hint, not proof of safe removal. Declared Python 3.14+ targets are handled by TYP001.",
        bad="from __future__ import annotations\n\ndef count(value: int) -> int:\n    return value",
        good="def count(value: int) -> int:\n    return value",
        fix="Review whether postponement is part of the module's runtime contract, then remove unnecessary boilerplate and verify types and behavior. On Python 3.12/3.13, an annotations future import is appropriate when the chosen public-first order uses forward references; LAY004 preserves eager dependencies when the import is absent. Keep the import if it serves a real purpose; annotate a justified exception if required. Self is only an alternative for methods whose return contract follows the receiver's subclass, such as returning self or constructing cls; a matching class-name annotation alone is insufficient. No Self rewrite is suggested automatically.",
        canonical=(
            "Drop from __future__ import annotations when no annotation needs"
            " postponing"
        ),
        config=("style.future_annotations", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        policy = _future_policy(ctx, target.module, target.absolute(ctx.root))
        if policy is None or policy.prohibited:
            return None
        return (
            "Write from __future__ import annotations only when an annotation "
            "needs postponing"
        )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        yield from _future_findings(self, ctx, forbid=False)


def _future_findings(
    rule: Rule, ctx: AnalysisContext, *, forbid: bool
) -> Iterator[Violation]:
    for source in ctx.files.sources.values():
        policy = _future_policy(ctx, source.module, source.absolute)
        if policy is None or policy.prohibited != forbid:
            continue
        prohibited = policy.prohibited
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
            not prohibited and has_postponement_evidence(tree, policy.python.minimum)
        ):
            continue
        for node in imports:
            yield rule.violation(
                f"{policy.contract.origin} forbids the annotations future import"
                if prohibited
                else "no static postponement need found; review the annotations future import",
                path=source.path,
                line=node.lineno,
                column=node.col_offset + 1,
                end_line=node.end_lineno,
                end_column=(node.end_col_offset or 0) + 1,
                source_module=source.module,
                expected={
                    "contract": policy.contract.name,
                    "future_annotations": policy.contract.spec.future_annotations,
                    "automatic_removal": False,
                    "python": policy.python.to_json(),
                },
                hint="Follow the package convention and verify forward references, runtime annotation consumers and tests before removing the import.",
            )


def _misplaced_loggers(tree: ast.Module) -> Iterator[tuple[ast.stmt, ast.Name]]:
    bindings = unshadowed_bindings(tree)
    if not {"logging", "logging.getLogger"}.intersection(bindings.values()):
        return

    body = (
        tree.body[1:] if ast.get_docstring(tree, clean=False) is not None else tree.body
    )
    in_header = True
    pending: list[tuple[ast.stmt, ast.Name]] = []
    for statement in body:
        if in_header and _is_header_import(statement):
            yield from pending
            pending.clear()
        elif (target := _module_logger_target(statement, bindings)) is None:
            in_header = False
        elif in_header:
            pending.append((statement, target))
        else:
            yield statement, target


def _logger_fix(
    source: SourceText, tree: ast.Module, statement: ast.stmt, *, name: str
) -> Fix | None:
    """Move the logger statement directly after the imports.

    Safe: it only reads imports and __name__, and binding it earlier lets later
    module code see it no later than before.
    """
    if binding_counts(tree)[name] != 1 or not source.stands_alone(statement):
        return None
    if isinstance(statement, ast.AnnAssign) and not _imported_names(
        tree, statement.annotation
    ):
        return None
    anchor = _header_end(tree)
    if anchor is None or anchor is statement:
        return None
    offset = source.line_end(anchor.end_lineno or anchor.lineno)
    if not isinstance(anchor, ast.Assign | ast.AnnAssign):
        # Keep the blank lines that separate the imports from the code.
        while (
            offset < len(source.text)
            and not source.text[offset : source.line_end_at(offset)].strip()
        ):
            offset = source.line_end_at(offset)
    start, end = source.lines(statement)
    if offset >= start:
        return None
    moved = source.text[start:end]
    if not moved.endswith("\n"):
        moved += "\n"
    if not isinstance(anchor, ast.Assign | ast.AnnAssign):
        # Separate the moved logger from the code it now precedes.
        moved += "\n"
    return Fix(
        Applicability.SAFE,
        f"Move module logger {name} directly after the imports",
        (source.insert(offset, moved), source.delete_lines(statement)),
    )


def _header_end(tree: ast.Module) -> ast.stmt | None:
    """The last import, or logger right after the imports, that opens the module."""
    bindings = unshadowed_bindings(tree)
    anchor: ast.stmt | None = None
    for index, statement in enumerate(tree.body):
        if (
            (index == 0 and is_docstring(statement))
            or _is_header_import(statement)
            or (anchor is not None and _module_logger_target(statement, bindings))
        ):
            anchor = statement
        else:
            break
    return anchor


def _module_logger_target(
    statement: ast.stmt, bindings: dict[str, str]
) -> ast.Name | None:
    match statement:
        case (
            ast.Assign(targets=[ast.Name() as target], value=value)
            | ast.AnnAssign(target=ast.Name() as target, value=value)
        ):
            pass
        case _:
            return None
    match value:
        case (
            ast.Call(func=func, args=[ast.Name(id="__name__")], keywords=[])
            | ast.Call(
                func=func,
                args=[],
                keywords=[ast.keyword(arg="name", value=ast.Name(id="__name__"))],
            )
        ) if qualified_name(func, bindings) == "logging.getLogger":
            return target
        case _:
            return None


def _is_header_import(statement: ast.stmt) -> bool:
    match statement:
        case ast.Import() | ast.ImportFrom():
            return True
        case ast.If(body=body, orelse=otherwise):
            return all(_is_header_import(node) for node in (*body, *otherwise))
        case ast.Try(body=body, handlers=handlers, orelse=otherwise, finalbody=final):
            return all(
                _is_header_import(node) for node in (*body, *otherwise, *final)
            ) and all(
                _is_header_import(node) or isinstance(node, ast.Pass)
                for handler in handlers
                for node in handler.body
            )
        case _:
            return False


@dataclass(frozen=True, slots=True)
class _FuturePolicy:
    contract: ResolvedContract
    python: PythonTarget
    prohibited: bool


def _future_policy(
    ctx: AnalysisContext, module: str, path: Path | None
) -> _FuturePolicy | None:
    """The governing future-annotations policy; None when it allows the import."""
    contract = ctx.contracts.policy_for(module, "future_annotations")
    if contract is None:
        return None
    mode = contract.spec.future_annotations
    if mode not in {"forbid", "when-needed"}:
        return None
    python = ctx.python_targets.for_path(path)
    prohibited = mode == "forbid" or python.deferred_annotations
    return _FuturePolicy(contract, python, prohibited=prohibited)


def _imported_names(tree: ast.Module, annotation: ast.expr) -> bool:
    """Whether an eagerly evaluated annotation only reads module imports."""
    bindings = unshadowed_bindings(tree)
    return all(
        node.id in bindings
        for node in tree_nodes(annotation)
        if isinstance(node, ast.Name)
    )
