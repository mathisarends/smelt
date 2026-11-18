from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.bindings import binding_counts
from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.function_usage import explicit_exports
from smelt.config.models import POLICY_DEFAULTS
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.config.models import ReexportStyle


class ReexportConvention(Rule):
    file_local = True
    activation = POLICIES["SMT369"]
    code = "SMT369"
    name = "visibility/reexport-convention"
    category = Category.VISIBILITY
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
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
            "in a style override with a reason. This is a read-only check."
        ),
        config=("rules", "style.reexports", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            if not source.is_package:
                continue
            contract = ctx.contracts.policy_for(source.module, "reexports")
            style = (
                contract.spec.reexports if contract else None
            ) or POLICY_DEFAULTS.reexports
            if style == "allow":
                continue
            using_all = style == "all"
            for alias in _mismatched_reexports(
                ctx.asts.parse(source.path), style=style
            ):
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
                )


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
