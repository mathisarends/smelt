from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.fixes import FixAvailability
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.analysis.contracts import ResolvedContract
    from smelt.analysis.python_version import PythonTarget


class MisplacedModuleLogger(Rule):
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


class ModuleDocstringConvention(Rule):
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


class ForbiddenFutureAnnotations(Rule):
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


class RedundantFutureAnnotations(Rule):
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
