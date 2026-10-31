from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.package_exports import direct_package_exports
from smelt.analysis.parsing import resolve_relative, type_checking_lines
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import is_within
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class PreferPackageImport(BaseRule):
    code = "SMT319"
    name = "prefer-package-import"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Prefer an existing package re-export over an implementation import.",
        rationale=(
            "Importing supported names from a common package keeps callers independent "
            "of implementation paths and allows related imports to be combined. This "
            "opt-in hint requires every name in a from-import to have an unconditional, "
            "direct, same-named public re-export in an analyzed ancestor __init__.py. "
            "The nearest eligible package is suggested. Static __all__ is respected. "
            "Imports inside that package are excluded to avoid routing its own "
            "implementation through its initializer. Imports in package initializers "
            "also stay unchanged. Conditional, rebound, private, "
            "star and dynamic exports, re-export chains, module imports and unscanned "
            "third-party packages are outside this check."
        ),
        bad=(
            "# app/observation/__init__.py exports Observe and Snapshot\n"
            "# app/client.py\nfrom app.observation import Observe\n"
            "from app.observation.models import Snapshot"
        ),
        good="# app/client.py\nfrom app.observation import Observe, Snapshot",
        fix=(
            "Use the suggested package import and combine it with an existing import "
            "there when both are in the same scope and guard. Preserve aliases, "
            "comments and relative/absolute import policy. Review initializer side "
            "effects and dependency cycles. No exports are created and no automatic "
            "rewrite is performed; analyzed code is never executed."
        ),
        config=("rules", "architecture.imports.type_checking"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        facades = {
            source.module: direct_package_exports(module)
            for source in ctx.files.sources.values()
            if source.is_package
            and (module := ctx.syntax.for_path(source.path)) is not None
        }
        for source in ctx.files.sources.values():
            if source.is_package:
                continue
            tree = ctx.asts.parse(source.path)
            guarded = type_checking_lines(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or any(
                    alias.name.startswith("_") or alias.name == "*"
                    for alias in node.names
                ):
                    continue
                if (
                    node.lineno in guarded
                    and ctx.config.architecture.imports.type_checking == "ignore"
                ):
                    continue
                base = resolve_relative(
                    source.module,
                    is_package=source.is_package,
                    level=node.level,
                    target=node.module,
                )
                if base not in ctx.files.sources:
                    continue
                package = base.rpartition(".")[0]
                while package:
                    exports = facades.get(package, {})
                    if not is_within(source.module, package) and all(
                        exports.get(alias.name) == f"{base}.{alias.name}"
                        for alias in node.names
                    ):
                        import_from = package
                        if node.level and node.module:
                            import_from = "." * node.level + node.module.removesuffix(
                                base.removeprefix(package)
                            )
                        replacement = f"from {import_from} import " + ", ".join(
                            ast.unparse(alias) for alias in node.names
                        )
                        yield self.violation(
                            f"import these names from the existing {package} package API",
                            path=source.path,
                            line=node.lineno,
                            column=node.col_offset + 1,
                            end_line=node.end_lineno,
                            end_column=(node.end_col_offset or node.col_offset) + 1,
                            source_module=source.module,
                            target_module=base,
                            expected={
                                "package": package,
                                "names": [alias.name for alias in node.names],
                                "replacement": replacement,
                                "automatic_rewrite": False,
                            },
                            hint=f"Use {replacement}; combine imports from {package} in the same scope and guard, preserving aliases and comments.",
                        )
                        break
                    package = package.rpartition(".")[0]
