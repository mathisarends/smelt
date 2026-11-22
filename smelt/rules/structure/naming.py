from __future__ import annotations

import ast
import keyword
from itertools import chain
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import tree_nodes
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc
from smelt.rules.common import last_segment, readable_list
from smelt.rules.structure.layout import package_location

if TYPE_CHECKING:
    from collections.abc import Iterator

_MIN_PLURAL_LENGTH = 4


class ForbiddenPackageName(Rule):
    activation = POLICIES["LAY001"]
    code = "LAY001"
    name = "layout/forbidden-module-name"
    category = Category.LAYOUT
    default_severity = Severity.ERROR
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A package or module name is listed in style.forbidden_names.",
        rationale=(
            "Names like utils or helpers describe nothing, so everything fits in them. "
            "They grow into grab bags that every layer imports."
        ),
        bad="voice/application/utils.py",
        good="voice/application/durations.py",
        fix="Name the module after what it does, or move each function next to its callers.",
        canonical=(
            "Name modules after what they do, never with a forbidden generic name"
        ),
        config=("style.forbidden_names",),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        del target
        names = ctx.config.structure.forbidden_names
        if not names:
            return None
        return (
            f"Never name a module or package {readable_list(names, conjunction='or')}; "
            "name it after what it does"
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


class ForbiddenPrivateModule(Rule):
    activation = POLICIES["LAY003"]
    code = "LAY003"
    name = "layout/forbidden-private-module"
    category = Category.LAYOUT
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
        canonical="Name modules and packages without a leading underscore",
        config=("style.private_modules", "style.overrides"),
        example_language="text",
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        contract = ctx.contracts.policy_for(target.module, "private_modules")
        if contract is None or contract.spec.private_modules != "forbid":
            return None
        return self.doc.canonical

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
                f'{contract.origin} forbids underscore-prefixed {kind} name "{segment}" ({name})',
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


class ForbiddenBaseClassAffix(Rule):
    file_local = True
    code = "MOD002"
    name = "modeling/forbidden-base-class-affix"
    category = Category.MODELING
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A class definition carries a Base prefix or suffix instead of a responsibility.",
        rationale=(
            "BaseService and MessageBase name a position in an inheritance "
            "hierarchy rather than the abstraction's responsibility. A "
            "responsibility-based name such as Service keeps that implementation "
            "choice out of the name. The affix does not itself create dependency "
            "coupling, and inheritance can be intentional, so this is an opt-in "
            "naming convention rather than a correctness check or an inheritance "
            "ban. It checks all class definitions in production modules, "
            "including private, nested and local classes, without inferring "
            "whether they have subclasses. After leading underscores, a prefix "
            "Base must be the whole name or be followed by an uppercase letter or "
            "underscore; a suffix Base must follow a non-empty name. Baseline, "
            "Baseball, Database and Base64 remain valid. Imports, aliases, "
            "base-class expressions, function names and test files are outside "
            "scope; inheriting from an imported BaseModel is allowed."
        ),
        bad=(
            "class BaseRepository:\n    ...\n\n"
            "class _MessageBase:\n    ...\n\n"
            "class SqlRepository(BaseRepository):\n    ..."
        ),
        good=(
            "class Repository:\n    ...\n\n"
            "class Message:\n    ...\n\n"
            "class SqlRepository(Repository):\n    ..."
        ),
        fix=(
            "Choose a name describing the class's responsibility or contract, "
            "then update references and deliberate exports. Preserve inheritance "
            "where it serves the design. A more specific package rule can "
            "disable the convention for compatibility, or a reasoned suppression "
            "can preserve a published name. No symbols are renamed "
            "automatically. Enable with rules: "
            "{modeling/forbidden-base-class-affix: error}, globally or in a "
            "style override, or --select MOD002."
        ),
        canonical=(
            "Name classes after their responsibility, without a Base prefix or suffix"
        ),
        config=("rules", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for name, source in sorted(ctx.files.sources.items()):
            info = ctx.model.info(name)
            for node in tree_nodes(ctx.asts.parse(source.path)):
                if not isinstance(node, ast.ClassDef):
                    continue
                affix = _base_affix(node.name)
                if affix is None:
                    continue
                yield self.violation(
                    f'class "{node.name}" uses the forbidden Base {affix}',
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    source_module=name,
                    feature=info.feature if info else None,
                    layer=info.layer if info else None,
                    expected={
                        "forbidden_affix": "Base",
                        "position": affix,
                        "class": node.name,
                        "automatic_rename": False,
                    },
                    hint="Name the class after its responsibility or contract instead of its inheritance role; update references and exports.",
                )


class RedundantModuleName(Rule):
    code = "LAY009"
    name = "layout/redundant-module-name"
    category = Category.LAYOUT
    default_severity = Severity.HINT
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A module filename repeats context already supplied by an ancestor package.",
        rationale=(
            "cars.application.car_exporter repeats the cars context; "
            "cars.application.exporter keeps the responsibility visible without "
            "restating the path. This opt-in review checks a whole ancestor "
            "package name at either underscore-delimited end of a module name, "
            "including across intervening layer packages. It also recognizes a "
            "single regular trailing s (cars/car, purchase_orders/purchase_order). "
            "It does not infer irregular plurals or strip ss, us, is, ies, ses, "
            "xes, zes, ches or shes endings. Leading underscores are preserved. "
            "Substrings, names with no remaining responsibility, package names, "
            "dunder modules and separately discovered tests are outside scope. "
            "Source-root directory names are not package context. Findings are "
            "advisory because repetition may serve a published API or domain meaning."
        ),
        bad="backend/features/cars/application/car_exporter.py",
        good="backend/features/cars/application/exporter.py",
        fix=(
            "Review the shorter filename and update imports and facade exports "
            "when renaming. An occupied module/package name is not suggested. "
            "No files are renamed automatically. Enable with rules: "
            "{layout/redundant-module-name: hint}, globally or in a style override, "
            "or --select LAY009. Use a package off override or a reasoned "
            "file suppression for intentional public names."
        ),
        canonical="Do not repeat the package name in a module file name",
        config=("rules", "style.overrides"),
        example_language="text",
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        files = ctx.files
        for module, source in sorted(files.sources.items()):
            if source.is_package:
                continue
            match = _redundant_module_context(module)
            if match is None:
                continue
            ancestor, repeated, shorter = match
            parent = module.rsplit(".", 1)[0]
            target = f"{parent}.{shorter}"
            suggested = (
                None
                if target in files.sources or target in files.packages
                else f"{shorter}.py"
            )
            info = ctx.model.info(module)
            yield self.violation(
                f'module name "{last_segment(module)}" repeats "{repeated}" from package "{ancestor}"',
                path=source.path,
                source_module=module,
                feature=info.feature if info else None,
                layer=info.layer if info else None,
                expected={
                    "ancestor": ancestor,
                    "repeated_name": repeated,
                    "suggested_name": suggested,
                    "automatic_rename": False,
                },
                hint=(
                    f'Review renaming this file to "{suggested}"; update imports and facade exports.'
                    if suggested
                    else "Choose a responsibility-based filename; the shorter name is already occupied by a module or package. Update imports and facade exports."
                ),
            )


def _redundant_module_context(module: str) -> tuple[str, str, str] | None:
    parts = module.split(".")
    name = parts[-1]
    if name.startswith("__") and name.endswith("__"):
        return None
    bare = name.lstrip("_")
    private_prefix = name[: len(name) - len(bare)]
    for end in range(len(parts) - 1, 0, -1):
        context = parts[end - 1].lstrip("_")
        if not context:
            continue
        variants = [context]
        if (
            len(context) >= _MIN_PLURAL_LENGTH
            and context.endswith("s")
            and not context.endswith(
                ("ss", "us", "is", "ies", "ses", "xes", "zes", "ches", "shes")
            )
        ):
            variants.append(context[:-1])
        for repeated in variants:
            shorter = None
            if bare.startswith(f"{repeated}_"):
                shorter = bare[len(repeated) + 1 :]
            elif bare.endswith(f"_{repeated}"):
                shorter = bare[: -len(repeated) - 1]
            if shorter and shorter.isidentifier() and not keyword.iskeyword(shorter):
                return ".".join(parts[:end]), repeated, private_prefix + shorter
    return None


def _base_affix(name: str) -> str | None:
    name = name.lstrip("_")
    rest = name.removeprefix("Base")
    if name.startswith("Base") and (not rest or rest[0].isupper() or rest[0] == "_"):
        return "prefix"
    if name.endswith("Base") and name[:-4].strip("_"):
        return "suffix"
    return None
