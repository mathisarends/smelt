from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc
from smelt.rules.common import readable_list


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


class ForbiddenBaseClassAffix(Rule):
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
        smell="A module file name repeats the name of a package above it, as in cars/application/car_exporter.py.",
        no_smell="A repetition that carries domain meaning or is a published import path; keep it with a reasoned file suppression or a package override set to off.",
        repair="Rename the file to the suggested shorter name and update imports and facade exports.",
        subject="rename to {suggested_name}",
    )
