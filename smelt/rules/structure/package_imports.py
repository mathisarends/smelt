from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.installed_exports import InstalledPackages
from smelt.analysis.package_exports import covering_package, direct_package_exports
from smelt.analysis.parsing import resolve_relative
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class PreferPackageImport(Rule):
    code = "IMP004"
    name = "imports/prefer-package-import"
    category = Category.IMPORTS
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    activation = POLICIES["IMP004"]
    doc = RuleDoc(
        summary="Prefer an existing package re-export over an implementation import.",
        rationale=(
            "Importing supported names from a common package keeps callers independent "
            "of implementation paths and allows related imports to be combined. This "
            "opt-in hint requires every name in a from-import to have an unconditional, "
            "direct, same-named public re-export in an analyzed ancestor __init__.py. "
            "The nearest eligible package is suggested. Static __all__ is respected. "
            "Production imports inside that package are excluded to avoid routing "
            "its own implementation through its initializer. Discovered tests and "
            "conftest.py are checked, including configured test roots inside the "
            "package. Test-quality exclusions do not exempt imports; project "
            "exclusions and normal suppressions apply. Imports in package initializers "
            "also stay unchanged. Conditional, rebound, private, "
            "star and dynamic exports, re-export chains and module imports are "
            "outside this check. Installed packages listed in third_party_reexports "
            "are read from the interpreter Smelt runs in, without importing them: "
            "stubs take precedence, star imports count when the target declares a "
            "static __all__, and names imported under TYPE_CHECKING count when a "
            "module __getattr__ serves them and __all__ lists them. Other "
            "third-party packages, and listed packages Smelt cannot find, are not "
            "checked."
        ),
        bad=(
            "# app/observation/__init__.py exports Observe and Snapshot\n"
            "# app/client.py\nfrom app.observation import Observe\n"
            "from app.observation.models import Snapshot"
        ),
        good=(
            "# app/client.py\nfrom app.observation import Observe, Snapshot\n"
            "# style: {third_party_reexports: [fastapi]}\nfrom fastapi import APIRouter"
        ),
        fix=(
            "Use the suggested package import and combine it with an existing import "
            "there when both are in the same scope and guard. Preserve aliases, "
            "comments and relative/absolute import policy. Review initializer side "
            "effects and dependency cycles. No exports are created and no automatic "
            "rewrite is performed; analyzed code is never executed."
        ),
        config=(
            "rules",
            "architecture.import_graph.type_checking",
            "project.test_roots",
            "style.third_party_reexports",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        facades = {
            source.module: direct_package_exports(module)
            for source in ctx.files.sources.values()
            if source.is_package
            and (module := ctx.syntax.for_path(source.path)) is not None
        }
        first_party = {module.partition(".")[0] for module in ctx.files.sources}
        installed = InstalledPackages()
        for path in ctx.files.all_python_paths():
            source = ctx.syntax.for_path(path)
            if source is None or source.is_package:
                continue
            for node in source.imports:
                if not isinstance(node, ast.ImportFrom) or any(
                    alias.name.startswith("_") or alias.name == "*"
                    for alias in node.names
                ):
                    continue
                if (
                    ctx.config.architecture.import_graph.type_checking == "ignore"
                    and node.lineno in source.guarded_lines
                ):
                    continue
                base = resolve_relative(
                    source.module,
                    is_package=source.is_package,
                    level=node.level,
                    target=node.module,
                )
                if base in ctx.files.sources:
                    lookup = facades
                elif node.level or base.partition(".")[0] in first_party:
                    continue
                else:
                    lookup = _installed_facades(
                        installed, base, third_party=_third_party(ctx, source.module)
                    )
                package = covering_package(
                    lookup,
                    base,
                    caller=source.module,
                    names=[alias.name for alias in node.names],
                    from_test=ctx.files.is_test_path(path),
                )
                if package is None:
                    continue
                import_from = package
                if node.level:
                    parts = node.module.split(".") if node.module else []
                    removed = len(base.split(".")) - len(package.split("."))
                    remaining = len(parts) - removed
                    import_from = "." * (node.level + max(0, -remaining)) + ".".join(
                        parts[: max(0, remaining)]
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


def _third_party(ctx: AnalysisContext, module: str) -> list[str]:
    contract = ctx.contracts.policy_for(module, "third_party_reexports")
    return (contract.spec.third_party_reexports or []) if contract else []


def _installed_facades(
    installed: InstalledPackages, base: str, *, third_party: list[str]
) -> dict[str, dict[str, str]]:
    """Exports of each installed ancestor of ``base`` in a listed package."""
    if base.partition(".")[0] not in third_party:
        return {}
    parts = base.split(".")
    return {
        package: installed.exports(package)
        for package in (".".join(parts[:end]) for end in range(1, len(parts)))
    }
