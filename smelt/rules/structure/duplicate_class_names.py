from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import SourceFile


@dataclass(frozen=True, slots=True)
class _Definition:
    source: SourceFile
    node: ast.ClassDef


class DuplicateClassName(Rule):
    code = "MOD009"
    name = "modeling/duplicate-class-name"
    category = Category.MODELING
    default_severity = Severity.WARNING
    production_only = True
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Two modules of one package define a public class with the same name.",
        rationale=(
            "When two modules of the same package each define a public class "
            "called PageContent, imports, search results and review comments "
            "stop saying which one is meant, and the wrong import still type "
            "checks when the shapes are similar. This opt-in review groups "
            "public top-level classes by their nearest package, counting a "
            "package's __init__.py as part of that package, and reports every "
            "class whose name is also defined at the top level of another "
            "module there. Re-exports, private classes, nested classes, "
            "classes in separate packages and tests are not compared. Each "
            "definition gets its own finding with the other locations."
        ),
        bad=(
            "# app/observation/models.py\n"
            "class PageContent:\n"
            "    html: str\n\n"
            "# app/observation/content.py\n"
            "class PageContent(Options):\n"
            "    markdown: str"
        ),
        good=(
            "# app/observation/models.py\n"
            "class CapturedPage:\n"
            "    html: str\n\n"
            "# app/observation/content.py\n"
            "class PageRead(Options):\n"
            "    markdown: str"
        ),
        fix=(
            "Rename the classes after their role so each name says which one "
            "is meant, or merge them when they describe the same thing. A "
            "class that only exists as a private implementation detail can "
            "take a leading underscore. No automatic rewrite is performed. "
            "Enable rules: {modeling/duplicate-class-name: warning}, or select MOD009."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        # package -> class name -> definitions
        groups: dict[str, dict[str, list[_Definition]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for source in ctx.files.sources.values():
            syntax = ctx.syntax.for_path(source.path)
            if syntax is None:
                continue
            package = (
                source.module if source.is_package else source.module.rpartition(".")[0]
            )
            for node in syntax.tree.body:
                if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                    groups[package][node.name].append(_Definition(source, node))
        for package, names in sorted(groups.items()):
            for name, definitions in sorted(names.items()):
                modules = {item.source.module for item in definitions}
                if len(modules) == 1:
                    continue
                for definition in definitions:
                    yield self._finding(
                        package, name, definition, definitions=definitions
                    )

    def _finding(
        self,
        package: str,
        name: str,
        definition: _Definition,
        *,
        definitions: list[_Definition],
    ) -> Violation:
        others = [
            {"module": other.source.module, "line": other.node.lineno}
            for other in definitions
            if other.source.module != definition.source.module
        ]
        where = ", ".join(f"{other['module']}:{other['line']}" for other in others)
        return self.violation(
            f"Class {name} is also defined in {where}",
            path=definition.source.path,
            line=definition.node.lineno,
            column=definition.node.col_offset + 1,
            source_module=definition.source.module,
            expected={
                "class": name,
                "package": package,
                "others": others,
                "automatic_rewrite": False,
            },
            hint=(
                f"Rename {name} after its role in {definition.source.module}, "
                "or merge the classes if they describe the same thing."
            ),
        )
