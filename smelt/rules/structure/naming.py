from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import last_segment
from smelt.rules.structure.layout import package_location

if TYPE_CHECKING:
    from collections.abc import Iterator


class ForbiddenPackageName(BaseRule):
    activation = POLICIES["SMT302"]
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


class NoPrivateModules(BaseRule):
    activation = POLICIES["SMT312"]
    code = "SMT312"
    name = "no-private-modules"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A module or package has an underscore prefix forbidden by its naming policy.",
        rationale=(
            "Some projects use descriptive module names and keep private helpers "
            "inside them. This opt-in naming convention rejects _service.py and "
            "_internal/ even when they export public symbols. __init__.py and "
            "__main__.py retain their Python-defined roles. Function, method, "
            "class and attribute names are outside this rule. Architecture API "
            "contracts apply independently of the spelling of a module name."
        ),
        bad="app/_service.py\napp/_internal/store.py",
        good="app/service.py\napp/storage/store.py\n\n# Inside service.py:\ndef run(): ...\ndef _prepare(): ...",
        fix=(
            "Choose a descriptive module or package name without a leading "
            "underscore, then update imports and deliberate facade exports. "
            "A more specific package can explicitly allow the convention. "
            "No files or symbols are renamed automatically."
        ),
        config=("structure.contracts",),
        example_language="text",
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        files = ctx.files
        entries = chain(
            (
                (name, package_location(files, package), "package")
                for name, package in sorted(files.packages.items())
            ),
            (
                (name, source.path, "module")
                for name, source in sorted(files.sources.items())
                if not source.is_package and last_segment(name) != "__main__"
            ),
        )
        for name, path, kind in entries:
            segment = last_segment(name)
            if not segment.startswith("_"):
                continue
            contract = ctx.contracts.policy_for(name, "private_modules")
            if contract is None or contract.spec.private_modules != "forbid":
                continue
            info = ctx.model.info(name)
            yield self.violation(
                f'contract "{contract.name}" forbids underscore-prefixed {kind} name "{segment}" ({name})',
                path=path,
                source_module=name,
                feature=info.feature if info else None,
                layer=info.layer if info else None,
                expected={
                    "contract": contract.name,
                    "private_modules": "forbid",
                    "kind": kind,
                    "name": segment,
                },
                hint=f"Rename the {kind} after its responsibility without a leading underscore; update imports and facade exports. Private helpers inside a normal module may keep their underscore prefix.",
            )
