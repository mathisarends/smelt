from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import ModuleKind
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import import_violation, join, matches_any, skip_import

if TYPE_CHECKING:
    from collections.abc import Iterator


class CompositionRootLeak(BaseRule):
    code = "SMT106"
    name = "composition-root-leak"
    category = Category.DEPENDENCIES
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary=(
            "A DI framework is imported outside the composition root, or a module "
            "imports the composition root."
        ),
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
        config=("architecture.composition_root", "architecture.di_frameworks"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        arch = ctx.config.architecture
        roots = arch.composition_root
        model = ctx.model
        for detail in ctx.imports.all_imports():
            if skip_import(ctx, detail):
                continue
            source = model.info(detail.importer)
            if source is None or source.kind is ModuleKind.COMPOSITION_ROOT:
                continue
            if detail.external:
                names = detail.names or (detail.imported,)
                framework = next(
                    (
                        e
                        for e in arch.di_frameworks
                        if any(matches_any(n, [e]) for n in names)
                    ),
                    None,
                )
                if framework is None:
                    continue
                yield import_violation(
                    self,
                    ctx,
                    detail,
                    f"{framework} may only be used in the composition root ({join(roots, 'none configured')})",
                    expected={"composition_root": list(roots)},
                    hint=(
                        "Accept dependencies as constructor parameters and do the wiring in "
                        f"{roots[0] if roots else 'a composition root'}."
                    ),
                )
                continue
            target = model.info(detail.imported)
            if target is None or target.kind is not ModuleKind.COMPOSITION_ROOT:
                continue
            yield import_violation(
                self,
                ctx,
                detail,
                f"{detail.importer} must not import the composition root {detail.imported}",
                expected={"composition_root": list(roots)},
                hint=(
                    "The composition root depends on everything; nothing depends on it. "
                    "Pass what you need in from the root instead."
                ),
            )
