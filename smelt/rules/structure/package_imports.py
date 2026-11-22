from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.installed_exports import InstalledPackages
from smelt.analysis.package_exports import covering_package, package_facades
from smelt.analysis.parsing import resolve_relative, tree_nodes
from smelt.diagnostics.fixes import Applicability, Fix, FixAvailability
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.edits import SourceText, rewrite_from_module

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.syntax import ImportStatement


class PreferPackageImport(Rule):
    code = "IMP004"
    name = "imports/prefer-package-import"
    category = Category.IMPORTS
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    activation = POLICIES["IMP004"]
    fix_availability = FixAvailability.SOMETIMES
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
            "effects and dependency cycles. No exports are created and analyzed "
            "code is never executed. check --fix --unsafe-fixes imports from the "
            "package, merging into an import of it in the same block when neither "
            "statement carries a comment."
        ),
        canonical=(
            "Import names through the package that re-exports them, not their"
            " implementation module"
        ),
        config=(
            "rules",
            "architecture.import_graph.type_checking",
            "project.test_roots",
            "style.third_party_reexports",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        facades = package_facades(ctx.syntax)
        first_party = {module.partition(".")[0] for module in ctx.files.sources}
        installed = InstalledPackages()
        for path in ctx.files.all_python_paths():
            source = ctx.syntax.module_identity(path)
            if source is None or source.is_package:
                continue
            for node in ctx.syntax.import_statements(path):
                if not node.from_import or any(
                    alias.name.startswith("_") or alias.name == "*"
                    for alias in node.names
                ):
                    continue
                if (
                    ctx.config.architecture.import_graph.type_checking == "ignore"
                    and node.guarded
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
                    alias.spelling for alias in node.names
                )
                fix = _fix(ctx, path, node, import_from=import_from)
                yield self.violation(
                    f"import these names from the existing {package} package API",
                    path=path,
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
                        "automatic_rewrite": fix is not None,
                    },
                    hint=f"Use {replacement}; combine imports from {package} in the same scope and guard, preserving aliases and comments.",
                    fix=fix,
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


def _fix(
    ctx: AnalysisContext, path: str, node: ImportStatement, *, import_from: str
) -> Fix | None:
    """Import from the package, joining an import of it in the same block.

    Unsafe: the package initializer now runs first, which can change import
    order, side effects and cycles.
    """
    text = SourceText(ctx.files.read_text(path))
    tree = ctx.asts.parse(path)
    found = _statement(tree, node)
    if found is None:
        return None
    statement, siblings = found
    current = "." * node.level + (node.module or "")
    level = len(import_from) - len(import_from.lstrip("."))
    module = import_from.lstrip(".") or None
    target = next(
        (
            sibling
            for sibling in siblings
            if sibling is not statement
            and isinstance(sibling, ast.ImportFrom)
            and sibling.level == level
            and sibling.module == module
            and sibling.names[0].name != "*"
            and "#" not in text.segment(sibling)
        ),
        None,
    )
    description = f"Import from {import_from}"
    if target is not None and text.stands_alone(statement):
        if "#" in text.text[slice(*text.lines(statement))]:
            return None
        present = {text.segment(alias) for alias in target.names}
        added = [
            spelling
            for alias in statement.names
            if (spelling := text.segment(alias)) not in present
        ]
        edits = [text.delete_lines(statement)]
        if added:
            end = text.span(target.names[-1]).end
            edits.append(text.insert(end, ", " + ", ".join(added)))
        return Fix(Applicability.UNSAFE, description, tuple(edits))
    edit = rewrite_from_module(
        text, text.span(statement).start, current=current, spelling=import_from
    )
    if edit is None:
        return None
    return Fix(Applicability.UNSAFE, description, (edit,))


def _statement(
    tree: ast.Module, node: ImportStatement
) -> tuple[ast.ImportFrom, list[ast.stmt]] | None:
    """The from-import at `node`'s position and the block that holds it."""
    for owner in tree_nodes(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(owner, field, None)
            if not isinstance(block, list):
                continue
            for statement in block:
                if (
                    isinstance(statement, ast.ImportFrom)
                    and statement.lineno == node.lineno
                    and statement.col_offset == node.col_offset
                ):
                    return statement, block
    return None
