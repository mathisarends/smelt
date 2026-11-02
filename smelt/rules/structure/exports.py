from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class PackageOnlyDunderAll(Rule):
    file_local = True
    code = "SMT325"
    name = "package-only-dunder-all"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="An ordinary module declares __all__ outside a package initializer.",
        rationale="This opt-in convention puts deliberate export lists in __init__.py so a package has one visible public facade. It reports module-scope __all__ bindings, including guarded, annotated, augmented and imported bindings. Function/class-local names and test files are outside scope. Package initializers may declare __all__; SMT313 independently checks definition order. This is an API convention, not a claim that Python forbids ordinary module exports.",
        bad='\n# app/events.py\ndef open_events(): ...\n__all__ = ["open_events"]',
        good='\n# app/events.py\ndef open_events(): ...\n\n# app/__init__.py\nfrom .events import open_events\n__all__ = ["open_events"]',
        fix="Review the package API and move the deliberate export list to __init__.py, preserving intended re-exports and checking cycles. Removing __all__ can change star imports and the visibility evidence used by other rules. No list is moved or removed automatically. Enable rules: {package-only-dunder-all: error}; closest package overrides can retain compatibility modules.",
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            if source.is_package:
                continue
            seen: set[int] = set()
            for node in _module_nodes(ctx.asts.parse(source.path)):
                if not isinstance(node, ast.Name | ast.alias):
                    continue
                binding = (
                    isinstance(node, ast.Name)
                    and isinstance(node.ctx, ast.Store)
                    and node.id == "__all__"
                )
                imported = (
                    isinstance(node, ast.alias)
                    and (node.asname or node.name) == "__all__"
                )
                if not (binding or imported) or node.lineno in seen:
                    continue
                seen.add(node.lineno)
                yield self.violation(
                    "declare __all__ in __init__.py instead of an ordinary module",
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "allowed_filename": "__init__.py",
                        "automatic_removal": False,
                    },
                    hint="Review moving the package export declaration to __init__.py; preserve star-import compatibility and check facade cycles.",
                )


def _module_nodes(node: ast.AST) -> Iterator[ast.AST]:
    if isinstance(
        node,
        ast.FunctionDef
        | ast.AsyncFunctionDef
        | ast.ClassDef
        | ast.Lambda
        | ast.ListComp
        | ast.SetComp
        | ast.DictComp
        | ast.GeneratorExp,
    ):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _module_nodes(child)
