from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.exports import resolve_dotted, resolve_export
from smelt.analysis.module_access import module_attributes
from smelt.analysis.parsing import resolve_relative, type_checking_lines
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import is_within
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class PrivateSymbolImport(Rule):
    code = "SMT339"
    name = "private-symbol-import"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Production code imports or reads another project module's private symbol.",
        rationale=(
            "A leading underscore marks a symbol as private to its module, including "
            "when callers are sibling modules in the same package. This opt-in review "
            "checks relative and absolute from-imports and module-qualified reads "
            "through lexical import aliases. Only symbols statically resolved to "
            "definitions in analyzed production modules are included. Third-party "
            "and unknown names, private module names, dunders, instance/class members, "
            "reflection and same-module access are excluded. Public aliases remain "
            "public. A package initializer may re-export its own submodule's private "
            "symbol under a public alias. Tests remain covered by SMT420. "
            "TYPE_CHECKING follows architecture.imports.type_checking. Rebound, "
            "shadowed or wildcard import bindings are conservatively skipped for "
            "qualified reads; dynamic assignments and arbitrary alias propagation "
            "are not inferred. No analyzed code is executed."
        ),
        bad="# app/client.py\nfrom .service import _normalize\n",
        good="# app/client.py\nfrom .service import normalize\n",
        fix=(
            "Give a shared helper a module-public name, keeping it out of __all__ "
            "and the package facade when it is internal to the package, or move it "
            "into the module that uses it. Review callers before renaming; no "
            "automatic rewrite is performed. Enable rules: {private-symbol-import: "
            "warning}, raise its severity to error, or select SMT339. Keep intentional "
            "access with a reasoned SMT339 suppression on the imported name or read."
        ),
        config=("rules", "structure.contracts", "architecture.imports.type_checking"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            module = ctx.syntax.for_path(source.path)
            if module is None:
                continue
            ignored = (
                type_checking_lines(module.tree)
                if ctx.config.architecture.imports.type_checking == "ignore"
                else frozenset()
            )
            reexports = (
                set(_module_imports(module.tree)) if source.is_package else set()
            )
            accesses: list[tuple[ast.alias | ast.Attribute, str, str, str]] = []
            for node in ast.walk(module.tree):
                if not isinstance(node, ast.ImportFrom) or node.lineno in ignored:
                    continue
                base = resolve_relative(
                    source.module,
                    is_package=source.is_package,
                    level=node.level,
                    target=node.module,
                )
                for alias in node.names:
                    if (
                        source.is_package
                        and node in reexports
                        and base != source.module
                        and is_within(base, source.module)
                        and alias.asname
                        and not alias.asname.startswith("_")
                    ):
                        continue
                    accesses.append((alias, base, alias.name, "import"))
            for attribute, qualified in module_attributes(module, ignored):
                resolved = resolve_dotted(qualified, ctx.syntax.namespace)
                if resolved is not None and resolved[1] is None:
                    accesses.append((attribute, resolved[0], attribute.attr, "read"))
            for location, base, name, access in accesses:
                if not _private(name):
                    continue
                target = resolve_export(base, name, ctx.syntax.namespace)
                if target is None or target[1] is None or target[0] == source.module:
                    continue
                qualified = f"{base}.{name}"
                yield self.violation(
                    f"production code accesses private module symbol {qualified}",
                    path=source.path,
                    line=location.lineno,
                    column=location.col_offset + 1,
                    end_line=location.end_lineno,
                    end_column=(location.end_col_offset or location.col_offset) + 1,
                    source_module=source.module,
                    target_module=base,
                    expected={
                        "private_target": qualified,
                        "defined_in": f"{target[0]}.{target[1]}",
                        "access": access,
                        "automatic_rewrite": False,
                    },
                    hint="Give the shared helper a module-public name without adding it to the package facade, or move it into the caller's module.",
                )


def _private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _module_imports(node: ast.AST) -> Iterator[ast.ImportFrom]:
    if isinstance(node, ast.ImportFrom):
        yield node
    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        for child in ast.iter_child_nodes(node):
            yield from _module_imports(child)
