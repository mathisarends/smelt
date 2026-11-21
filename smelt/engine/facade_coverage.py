"""Diagnose package facades too empty for an enabled IMP004 to check anything."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smelt.analysis.context import Index
from smelt.analysis.package_exports import covering_package, direct_package_exports
from smelt.analysis.parsing import resolve_relative
from smelt.model import is_within
from smelt.rules.meta import UncoveredPackageFacade
from smelt.rules.structure.package_imports import PreferPackageImport

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.context import AnalysisContext
    from smelt.diagnostics.violation import Severity, Violation
    from smelt.engine.rule_settings import RuleSettings


def uncovered_imports(
    ctx: AnalysisContext, settings: RuleSettings
) -> Iterator[Violation]:
    """IMP004-coded evidence: outside imports no eligible facade can supply.

    The caller filters them through ignore entries like real IMP004 findings
    before :func:`uncovered_facades` counts them.
    """
    rule = PreferPackageImport()
    if not settings.has_explicit_activation(rule.code):
        return
    ctx.ensure(frozenset({Index.SYNTAX}))
    facades = {
        source.module: direct_package_exports(module)
        for source in ctx.files.sources.values()
        if source.is_package and (module := ctx.syntax.for_path(source.path))
    }
    ignore_guarded = ctx.config.architecture.import_graph.type_checking == "ignore"
    for source in ctx.files.sources.values():
        if source.is_package or ctx.files.is_test_path(source.path):
            continue
        if (
            settings.explicit_activation(
                rule.code, ctx, module=source.module, path=source.path
            )
            is None
        ):
            continue
        module = ctx.syntax.for_path(source.path)
        if module is None:
            continue
        for node in module.imports:
            if not isinstance(node, ast.ImportFrom) or any(
                alias.name.startswith("_") or alias.name == "*" for alias in node.names
            ):
                continue
            if ignore_guarded and node.lineno in module.guarded_lines:
                continue
            base = resolve_relative(
                source.module, is_package=False, level=node.level, target=node.module
            )
            target = ctx.files.sources.get(base)
            if target is None or target.is_package:
                continue
            names = [alias.name for alias in node.names]
            if covering_package(facades, base, caller=source.module, names=names):
                continue  # IMP004 itself reports this statement.
            package = _enclosing_facade(facades, base, caller=source.module)
            if package is None:
                continue
            exports = facades[package]
            yield rule.violation(
                f"{package} cannot supply this import",
                path=source.path,
                line=node.lineno,
                column=node.col_offset + 1,
                source_module=source.module,
                target_module=base,
                expected={
                    "package": package,
                    "unresolved": [
                        name for name in names if exports.get(name) != f"{base}.{name}"
                    ],
                },
            )


def uncovered_facades(
    ctx: AnalysisContext,
    evidence: list[Violation],
    settings: RuleSettings,
    *,
    severity: Severity,
) -> Iterator[Violation]:
    """One hint per facade whose uncovered outside callers exceed the limit."""
    limit = ctx.config.max_uncovered_facade_callers
    packages: dict[str, _Evidence] = {}
    for item in evidence:
        if item.expected is None or item.source_module is None:
            continue
        found = packages.setdefault(str(item.expected["package"]), _Evidence())
        found.callers.add(item.source_module)
        found.modules.add(item.target_module or "")
        found.names.update(
            f"{item.target_module}.{name}" for name in item.expected["unresolved"]
        )
    diagnostic = UncoveredPackageFacade()
    for package, found in sorted(packages.items()):
        initializer = ctx.files.sources[package]
        if len(found.callers) <= limit or _governed_by_export_gap(
            ctx, settings, package=package, path=initializer.path
        ):
            continue
        callers = sorted(found.callers)
        names = sorted(found.names)
        yield diagnostic.violation(
            f"{package} does not re-export {len(names)} names that "
            f"{len(callers)} outside modules import from "
            f"{len(found.modules)} implementation modules, "
            "so imports/prefer-package-import cannot apply",
            path=initializer.path,
            line=1,
            column=1,
            source_module=package,
            expected={
                "package": package,
                "callers": len(callers),
                "implementation_modules": len(found.modules),
                "names": len(names),
                "sample_callers": callers[:_SAMPLE],
                "sample_names": names[:_SAMPLE],
                "max": limit,
            },
            hint=(
                "Decide the package API: re-export these names deliberately (this can "
                "change import-time behavior), or turn imports/prefer-package-import "
                "off for this package or ignore this hint with a reason."
            ),
            edge=f"{diagnostic.code}:{package}",
        ).with_severity(severity)


_EXPORT_GAP = "ARC008"
_SAMPLE = 5


@dataclass(slots=True)
class _Evidence:
    callers: set[str] = field(default_factory=set)
    modules: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)


def _enclosing_facade(
    facades: dict[str, dict[str, str]], base: str, *, caller: str
) -> str | None:
    """The nearest package with an initializer around ``base`` that excludes ``caller``."""
    package = base.rpartition(".")[0]
    while package and not is_within(caller, package):
        if package in facades:
            return package
        package = package.rpartition(".")[0]
    return None


def _governed_by_export_gap(
    ctx: AnalysisContext, settings: RuleSettings, *, package: str, path: str
) -> bool:
    """Whether an active ARC008 export threshold already reviews this package."""
    rule = settings.rules.by_code(_EXPORT_GAP)
    return (
        rule is not None
        and any(
            contract.spec.min_importers_to_export is not None
            and is_within(package, contract.package)
            for contract in ctx.contracts.resolved
        )
        and settings.severity(rule, ctx, package, path=path) is not None
    )
