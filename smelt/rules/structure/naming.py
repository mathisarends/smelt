from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import last_segment
from smelt.rules.structure.layout import package_location

if TYPE_CHECKING:
    from collections.abc import Iterator


class ForbiddenPackageName(BaseRule):
    code = "SMT302"
    name = "forbidden-package-name"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A package or module name is listed in structure.forbidden_names.",
        rationale=(
            "Names like utils or helpers describe nothing, so everything fits in them. "
            "They grow into grab bags that every layer imports."
        ),
        bad="voice/application/utils.py",
        good="voice/application/durations.py",
        fix="Name the module after what it does, or move each function next to its callers.",
        config=("structure.forbidden_names",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        forbidden = set(ctx.config.structure.forbidden_names)
        if not forbidden:
            return
        files = ctx.files
        model = ctx.model
        for name, package in sorted(files.packages.items()):
            if last_segment(name) in forbidden:
                info = model.info(name)
                yield self.violation(
                    f'package name "{last_segment(name)}" is not allowed ({name})',
                    path=package_location(files, package),
                    source_module=name,
                    feature=info.feature if info else None,
                    layer=info.layer if info else None,
                    expected={"forbidden_names": sorted(forbidden)},
                    hint="Name the package after what it does.",
                )
        for name, source in sorted(files.sources.items()):
            if source.is_package or last_segment(name) not in forbidden:
                continue
            info = model.info(name)
            yield self.violation(
                f'module name "{last_segment(name)}" is not allowed ({name})',
                path=source.path,
                source_module=name,
                feature=info.feature if info else None,
                layer=info.layer if info else None,
                expected={"forbidden_names": sorted(forbidden)},
                hint="Name the module after what it does, or move its functions next to their callers.",
            )
