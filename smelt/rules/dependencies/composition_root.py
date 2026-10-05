from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import ModuleKind
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import (
    import_violation,
    skip_import,
    wiring_facade,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


class CompositionRootLeak(BaseRule):
    code = "SMT106"
    name = "composition-root-leak"
    category = Category.DEPENDENCIES
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary="A module imports the composition root or a declared wiring module.",
        rationale=(
            "Wiring belongs in one place. When features reach for the container or the "
            "bootstrap module, construction logic spreads and dependencies become hidden."
        ),
        bad="# voice/application/session.py\nfrom dishka import FromDishka",
        good=(
            "# voice/application/session.py\n"
            "class StartSession:\n"
            "    def __init__(self, sessions: VoiceSessionRepository) -> None: ..."
        ),
        fix=(
            "Receive dependencies through constructor parameters and register them in the "
            "composition root."
        ),
        config=(
            "architecture.composition_root",
            "architecture.wiring",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        roots = ctx.config.architecture.composition_root
        model = ctx.model
        for detail in ctx.imports.all_imports():
            if detail.external or skip_import(ctx, detail):
                continue
            source = model.info(detail.importer)
            if (
                source is None
                or source.kind is ModuleKind.COMPOSITION_ROOT
                or source.wiring
            ):
                continue
            target = model.info(detail.imported)
            if target is None or (
                target.kind is not ModuleKind.COMPOSITION_ROOT and not target.wiring
            ):
                continue
            if wiring_facade(model, source, target):
                continue
            label = "wiring module" if target.wiring else "composition root"
            yield import_violation(
                self,
                ctx,
                detail,
                f"{detail.importer} must not import the {label} {detail.imported}",
                expected={"composition_root": list(roots)},
                hint=(
                    "The composition root depends on everything; nothing depends on it. "
                    "Pass what you need in from the root instead."
                ),
            )
