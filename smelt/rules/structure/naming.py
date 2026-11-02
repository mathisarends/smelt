from __future__ import annotations

import ast
from itertools import chain
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.common import last_segment
from smelt.rules.structure.layout import package_location

if TYPE_CHECKING:
    from collections.abc import Iterator


class ForbiddenPackageName(Rule):
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


class NoPrivateModules(Rule):
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


class NoBaseClassPrefix(Rule):
    file_local = True
    code = "SMT323"
    name = "no-base-class-prefix"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A class definition uses the Base prefix forbidden by the selected naming convention.",
        rationale=(
            "BaseService names a position in an inheritance hierarchy rather than "
            "the abstraction's responsibility. A responsibility-based name such as "
            "Service keeps that implementation choice out of the name. The prefix "
            "does not itself create additional dependency coupling, and inheritance "
            "can be intentional, so this is an opt-in naming convention rather than "
            "a correctness check or an inheritance ban. It checks all class "
            "definitions in matching production modules, including private, nested "
            "and local classes, without inferring whether they have subclasses. "
            "After leading underscores, Base must be the whole name or be followed "
            "by an uppercase letter or underscore. Baseline, Baseball and Base64 "
            "remain valid. Imports, aliases, function names and test files are "
            "outside scope; inheriting from an imported BaseModel is allowed."
        ),
        bad="class BaseRepository:\n    ...\n\nclass SqlRepository(BaseRepository):\n    ...",
        good="class Repository:\n    ...\n\nclass SqlRepository(Repository):\n    ...",
        fix=(
            "Choose a name describing the class's responsibility or contract, "
            "then update references and deliberate exports. Preserve inheritance "
            "where it serves the design. A more specific package rule can "
            "disable the convention for compatibility. No symbols are "
            "renamed automatically. Enable with rules: "
            "{no-base-class-prefix: error}, globally or in a package contract, "
            "or --select SMT323."
        ),
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for name, source in sorted(ctx.files.sources.items()):
            info = ctx.model.info(name)
            for node in ast.walk(ctx.asts.parse(source.path)):
                if not isinstance(node, ast.ClassDef) or not _has_base_prefix(
                    node.name
                ):
                    continue
                yield self.violation(
                    f'class "{node.name}" uses the forbidden Base prefix',
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    source_module=name,
                    feature=info.feature if info else None,
                    layer=info.layer if info else None,
                    expected={
                        "forbidden_prefix": "Base",
                        "class": node.name,
                        "automatic_rename": False,
                    },
                    hint="Name the class after its responsibility or contract instead of its inheritance role; update references and exports.",
                )


class NoBaseClassSuffix(Rule):
    file_local = True
    code = "SMT326"
    name = "no-base-class-suffix"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A class definition ends in the Base suffix forbidden by the selected convention.",
        rationale="MessageBase and _MessageBase describe an inheritance role instead of the abstraction's responsibility. This opt-in naming convention checks production class definitions, including private, nested and local classes. Imported names, aliases, base-class expressions and test files are not renamed or rejected. It complements SMT323 without changing that rule's prefix contract. A bare Base belongs to SMT323; Database, Base64 and responsibility-based class names remain valid.",
        bad="class _MessageBase:\n    ...\n\nclass AssistantMessage(_MessageBase):\n    ...",
        good="class Message:\n    ...\n\nclass AssistantMessage(Message):\n    ...",
        fix="Name the class after its responsibility or contract and update references and deliberate exports. Keep intentional inheritance. Enable rules: {no-base-class-suffix: error}; package overrides or a reasoned suppression can preserve a published name. No automatic rename is performed.",
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            for node in ast.walk(ctx.asts.parse(source.path)):
                if (
                    not isinstance(node, ast.ClassDef)
                    or not node.name.endswith("Base")
                    or not node.name[:-4].strip("_")
                ):
                    continue
                yield self.violation(
                    f'class "{node.name}" uses the forbidden Base suffix',
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "forbidden_suffix": "Base",
                        "class": node.name,
                        "automatic_rename": False,
                    },
                    hint="Name the class after its responsibility or contract instead of its inheritance role; update references and exports.",
                )


def _has_base_prefix(name: str) -> bool:
    name = name.lstrip("_")
    suffix = name.removeprefix("Base")
    return name.startswith("Base") and (
        not suffix or suffix[0].isupper() or suffix[0] == "_"
    )
