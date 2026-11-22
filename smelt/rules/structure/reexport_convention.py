from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.bindings import binding_counts
from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.function_usage import explicit_exports
from smelt.config.models import POLICY_DEFAULTS
from smelt.diagnostics.fixes import Applicability, Fix, FixAvailability, TextEdit
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc
from smelt.rules.edits import SourceText, import_insertion

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.config.models import ReexportStyle


class ReexportConvention(Rule):
    file_local = True
    activation = POLICIES["VIS008"]
    code = "VIS008"
    name = "visibility/reexport-convention"
    category = Category.VISIBILITY
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="A package initializer spells a re-export against its configured convention.",
        rationale=(
            "Use style.reexports to give package APIs a consistent spelling. "
            "all reports explicit same-name import aliases and uses __all__ for "
            "the public export list. alias reports ordinary imports named by a "
            "statically resolved __all__ when a same-name alias can mark the "
            "re-export. Existing aliases can coexist with an intentional runtime "
            "export list. The rule considers unique, direct import bindings in "
            "production __init__.py files. Real renames, dotted module imports, "
            "guarded or rebound names and star imports are outside its inference. "
            "Selecting the rule without a style uses all."
        ),
        bad=(
            "# style: {reexports: all}\n"
            "# app/events/__init__.py\n"
            "from .models import Event as Event"
        ),
        good='# app/events/__init__.py\nfrom .models import Event\n\n__all__ = ["Event"]',
        fix=(
            "With all, use a plain import and include the name in __all__ if it "
            "belongs to the public API. With alias, use a same-name alias such as "
            "Event as Event and preserve an intentional runtime export list. "
            "For a package that needs either spelling, set reexports to allow "
            "in a style override with a reason. check --fix adds the same-name "
            "alias, or drops it where __all__ already lists the name; adding the "
            "name to __all__, or creating __all__, changes what star imports "
            "export and needs --unsafe-fixes. A dynamic __all__ is left for a "
            "manual edit."
        ),
        canonical="Follow the package's re-export convention in __init__.py",
        config=("rules", "style.reexports", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        style = _reexport_style(ctx, target.module)
        if style == "all":
            return "Re-export in __init__.py with plain imports listed in __all__"
        if style == "alias":
            return "Re-export in __init__.py with same-name aliases (from .x import A as A)"
        return None

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            if not source.is_package:
                continue
            style = _reexport_style(ctx, source.module)
            if style == "allow":
                continue
            using_all = style == "all"
            tree = ctx.asts.parse(source.path)
            mismatched = list(_mismatched_reexports(tree, style=style))
            text = SourceText(ctx.files.read_text(source.path)) if mismatched else None
            for alias in mismatched:
                fix = (
                    (
                        _all_fix(text, tree, alias=alias, mismatched=mismatched)
                        if using_all
                        else _alias_fix(text, alias)
                    )
                    if text is not None
                    else None
                )
                yield self.violation(
                    f"use a plain import for {alias.name} under the __all__ convention"
                    if using_all
                    else f"mark the re-export of {alias.name} with a same-name alias",
                    path=source.path,
                    line=alias.lineno,
                    column=alias.col_offset + 1,
                    source_module=source.module,
                    expected={"style": style, "name": alias.name},
                    hint=(
                        f"Use a plain import and list {alias.name} in __all__ if it belongs to the package API."
                        if using_all
                        else f"Use {alias.name} as {alias.name}. Keep __all__ where callers rely on that list."
                    ),
                    fix=fix,
                )


def _alias_fix(source: SourceText, alias: ast.alias) -> Fix:
    """Safe: a same-name alias binds the same object and only marks the export."""
    return Fix(
        Applicability.SAFE,
        f"Re-export {alias.name} as {alias.name}",
        (source.insert(source.span(alias)[1], f" as {alias.name}"),),
    )


def _all_fix(
    source: SourceText,
    tree: ast.Module,
    *,
    alias: ast.alias,
    mismatched: list[ast.alias],
) -> Fix | None:
    """Drop the same-name alias and make sure __all__ lists the name.

    Safe when __all__ already lists it. Otherwise every mismatched name joins
    __all__ in one edit, which widens or narrows what star imports export.
    """
    exports = explicit_exports(tree)
    if exports is None:
        return None
    start, end = source.span(alias)
    edits = [TextEdit.at(source.text, (start + len(alias.name), end), "")]
    if alias.name in exports:
        return Fix(
            Applicability.SAFE, f"Import {alias.name} without the alias", tuple(edits)
        )
    missing = list(
        dict.fromkeys(item.name for item in mismatched if item.name not in exports)
    )
    edits.append(_export_edit(source, tree, missing))
    return Fix(
        Applicability.UNSAFE,
        f"Import {alias.name} without the alias and list it in __all__",
        tuple(edits),
    )


def _export_edit(source: SourceText, tree: ast.Module, names: list[str]) -> TextEdit:
    """Append `names` to the static __all__, or create one after the imports."""
    value = next(
        (
            statement.value
            for statement in reversed(tree.body)
            if isinstance(statement, ast.Assign | ast.AnnAssign | ast.AugAssign)
            and isinstance(statement.value, ast.List | ast.Tuple | ast.Set)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
            )
        ),
        None,
    )
    if value is None:
        listed = ", ".join(f'"{name}"' for name in names)
        return source.insert_line(
            import_insertion(source, tree), f"\n__all__ = [{listed}]"
        )
    quote = source.segment(value.elts[0])[0] if value.elts else '"'
    listed = ", ".join(f"{quote}{name}{quote}" for name in names)
    if value.elts:
        return source.insert(source.span(value.elts[-1])[1], f", {listed}")
    match value:
        case ast.Tuple():
            return source.replace(value, f"({listed},)")
        case ast.Set():
            return source.replace(value, f"{{{listed}}}")
        case _:
            return source.replace(value, f"[{listed}]")


def _mismatched_reexports(
    tree: ast.Module, *, style: ReexportStyle
) -> Iterator[ast.alias]:
    exports = explicit_exports(tree) if style == "alias" else frozenset()
    counts = binding_counts(tree)
    if exports is None or counts["*"]:
        return
    for statement in tree.body:
        if not isinstance(statement, ast.Import | ast.ImportFrom) or (
            isinstance(statement, ast.ImportFrom)
            and statement.level == 0
            and statement.module == "__future__"
        ):
            continue
        for alias in statement.names:
            if (
                "." in alias.name
                or alias.name in {"__all__", "__getattr__", "__dir__"}
                or counts[alias.asname or alias.name] != 1
            ):
                continue
            if (style == "all" and alias.asname == alias.name) or (
                style == "alias" and alias.asname is None and alias.name in exports
            ):
                yield alias


def _reexport_style(ctx: AnalysisContext, module: str) -> ReexportStyle:
    contract = ctx.contracts.policy_for(module, "reexports")
    return (contract.spec.reexports if contract else None) or POLICY_DEFAULTS.reexports
