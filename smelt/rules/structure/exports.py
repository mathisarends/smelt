from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.function_usage import explicit_exports
from smelt.analysis.parsing import child_nodes
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class PackageOnlyDunderAll(Rule):
    file_local = True
    code = "VIS003"
    name = "visibility/misplaced-dunder-all"
    category = Category.VISIBILITY
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="An ordinary module declares __all__ outside a package initializer.",
        rationale="This opt-in convention puts deliberate export lists in __init__.py so a package has one visible public facade. It reports module-scope __all__ bindings, including guarded, annotated, augmented and imported bindings. Function/class-local names and test files are outside scope. Package initializers may declare __all__; LAY004 independently checks definition order. This is an API convention, not a claim that Python forbids ordinary module exports.",
        bad='\n# app/events.py\ndef open_events(): ...\n__all__ = ["open_events"]',
        good='\n# app/events.py\ndef open_events(): ...\n\n# app/__init__.py\nfrom .events import open_events\n__all__ = ["open_events"]',
        fix="Review the package API and move the deliberate export list to __init__.py, preserving intended re-exports and checking cycles. Removing __all__ can change star imports and the visibility evidence used by other rules. No list is moved or removed automatically. Enable rules: {visibility/misplaced-dunder-all: error}; closest package overrides can retain compatibility modules.",
        canonical="Declare __all__ only in package __init__.py files",
        config=("rules", "style.overrides"),
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


class ReexportOnlyModule(Rule):
    file_local = True
    code = "LAY010"
    name = "layout/reexport-only-module"
    category = Category.LAYOUT
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="An ordinary module consists entirely of explicit re-exports.",
        rationale=(
            "A module that only forwards names adds another import path to maintain. "
            "This opt-in hint looks for direct imports marked by same-name aliases "
            "or a single literal __all__ list or tuple covering every import binding. "
            "A module docstring and future imports are allowed. The check is limited "
            "to ordinary production modules with distinct, unambiguous bindings. "
            "Package initializers, star imports, rebinding, conditional imports and "
            "dynamic export declarations fall outside this source pattern."
        ),
        bad=(
            "# app/events_api.py\nfrom .events import Event as Event\n\n"
            "# app/client.py\nfrom .events_api import Event"
        ),
        good="# app/client.py\nfrom .events import Event",
        fix=(
            "Review moving callers to the defining module or an intentional package "
            "API. A supported compatibility path can use a reasoned suppression. "
            "Check published import paths, side effects and cycles before removing "
            "a forwarding module. This is a read-only review."
        ),
        canonical=(
            "Import from the defining module or the package API; do not add "
            "re-export-only modules"
        ),
        config=("rules", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            if source.is_package:
                continue
            bindings = _reexport_bindings(ctx.asts.parse(source.path))
            if not bindings:
                continue
            first = next(iter(bindings.values()))
            yield self.violation(
                f"{source.module} only re-exports imported names",
                path=source.path,
                line=first.lineno,
                column=first.col_offset + 1,
                source_module=source.module,
                expected={"reexported_names": sorted(bindings)},
                hint="Review using the defining module or package API directly. Preserve supported import paths with a reasoned exception.",
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
    for child in child_nodes(node):
        yield from _module_nodes(child)


def _reexport_bindings(tree: ast.Module) -> dict[str, ast.alias]:
    body = (
        tree.body[1:] if ast.get_docstring(tree, clean=False) is not None else tree.body
    )
    bindings: dict[str, ast.alias] = {}
    declares_all = False
    for node in body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module == "__future__"
        ):
            continue
        if not declares_all and _literal_export_assignment(node):
            declares_all = True
            continue
        if not isinstance(node, ast.Import | ast.ImportFrom):
            return {}
        for alias in node.names:
            name = alias.asname or alias.name.partition(".")[0]
            if (
                alias.name == "*"
                or name in bindings
                or name in {"__all__", "__getattr__", "__dir__"}
            ):
                return {}
            bindings[name] = alias
    explicit = (
        bindings.keys() == explicit_exports(tree)
        if declares_all
        else all(alias.asname == alias.name for alias in bindings.values())
    )
    return bindings if explicit else {}


def _literal_export_assignment(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__all__"
        and isinstance(node.value, ast.List | ast.Tuple)
    )
