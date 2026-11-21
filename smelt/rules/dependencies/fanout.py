from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, TypedDict

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.facade_usage import FacadeUsage
from smelt.analysis.graphs import shortest_cycle
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import is_within
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.common import import_violation, skip_import
from smelt.rules.dependencies.insiders import inside_contract

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.imports import ImportDetail


class PackageImportFanout(Rule):
    activation = POLICIES["IMP001"]
    code = "IMP001"
    name = "imports/excess-package-imports"
    category = Category.IMPORTS
    default_severity = Severity.HINT
    requires = frozenset({Index.IMPORTS, Index.SYNTAX})
    doc = RuleDoc(
        summary="One caller spreads a package API across several import paths.",
        rationale=(
            "Repeated imports of neighboring implementation modules can indicate a "
            "missing package API. This is an opt-in design hint, not proof of poor "
            "architecture. Count distinct target modules per source file and contracted "
            "package, including its facade when mixed with implementation imports, "
            "not statements, symbols, or imports across the whole repository. "
            "Findings include missing static exports and a cycle witness for the "
            "proposed caller/facade edges. Known cycles block bundling advice. "
            "No-cycle-found covers only explicit runtime imports; dynamic imports "
            "and implicit package initialization remain unproven. Discovered tests "
            "and conftest.py also count as outside callers, including configured "
            "test roots inside the package, unless the contract's test_access: "
            "mirrored makes a test mirroring one of its modules an insider. Test-quality exclusions do not exempt "
            "imports; project exclusions and normal suppressions apply. This does "
            "not change ARC008's separate count_tests opt-in for API expansion."
        ),
        bad="# caller.py\nfrom app.users.application.get_user import GetUser\nfrom app.users.application.update_user import UpdateUser\nfrom app.users.application.delete_user import DeleteUser",
        good="# caller.py, after deliberate facade exports are defined\nfrom app.users.application import DeleteUser, GetUser, UpdateUser",
        fix=(
            "Consider exporting the caller's supported API from the package facade. "
            "Use proposed_exports to review missing re-exports. A cycle_check of "
            "cycle blocks bundling: keep the imports or resolve dependency direction "
            "first. No automatic rewrite is performed. If these "
            "independent dependencies are intentional, retain them or adjust the "
            "advisory threshold. Declare public: ['.'] to enforce facade-only imports."
        ),
        config=(
            "architecture.packages",
            "architecture.import_graph.type_checking",
            "project.test_roots",
            "tests.mirror",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        usage: FacadeUsage | None = None
        for contract in ctx.contracts.facade_policies("min_imports_to_bundle"):
            threshold = contract.spec.min_imports_to_bundle
            if threshold is None:
                continue
            callers: dict[str, dict[str, ImportDetail]] = defaultdict(dict)
            for path, detail in ctx.package_api_imports:
                if (
                    detail.external
                    or skip_import(ctx, detail)
                    or inside_contract(ctx, path, detail.importer, contract=contract)
                    or not is_within(detail.imported, contract.package)
                ):
                    continue
                callers[path].setdefault(detail.imported, detail)
            for path, targets in sorted(callers.items()):
                if len(targets) < threshold:
                    continue
                first = min(
                    targets.values(), key=lambda detail: (detail.line, detail.column)
                )
                caller = first.importer
                if usage is None:
                    usage = FacadeUsage(
                        ctx.syntax, ctx.imports, files=ctx.files, count_tests=True
                    )
                evidence = _bundle_evidence(
                    ctx,
                    usage,
                    path,
                    package=contract.package,
                    targets=targets,
                )
                blocked = evidence["cycle_check"] == "cycle"
                yield import_violation(
                    self,
                    ctx,
                    first,
                    path=path,
                    message=f"{caller} imports {len(targets)} modules of {contract.package}; consider its package facade",
                    expected={
                        "contract": contract.name,
                        "package": contract.package,
                        "min_imports_to_bundle": threshold,
                        "modules": sorted(targets),
                        "count": len(targets),
                        **evidence,
                    },
                    hint=(
                        "Bundling is blocked by a known runtime import cycle: "
                        + " -> ".join(evidence["cycle"])
                        if blocked
                        else f"Review deliberate exports from {contract.package}, then combine imports in the same scope and guard. No cycle was found in the explicit runtime graph; review initialization before changing exports."
                    ),
                    edge=f"{caller} -> {contract.package} ({contract.name})",
                )


class _BundleEvidence(TypedDict):
    cycle_check: str
    cycle: list[str]
    proposed_exports: list[dict[str, object]]
    blocked_exports: list[dict[str, object]]
    automatic_rewrite: bool
    initialization_safety: str


def _bundle_evidence(
    ctx: AnalysisContext,
    usage: FacadeUsage,
    path: str,
    *,
    package: str,
    targets: dict[str, ImportDetail],
) -> _BundleEvidence:
    caller = next(iter(targets.values())).importer
    test = ctx.files.is_test_path(path)
    exports = [
        {
            "definition": f"{gap.module}.{gap.name}",
            "name": gap.name,
            "suggested_import": f"from .{gap.module.removeprefix(package + '.')} import {gap.name}",
            "needs_import": gap.needs_import,
        }
        for gap in usage.gaps(package, count_tests=True)
        if caller in (gap.test_importers if test else gap.source_importers)
    ]
    adjacency = {
        name: set(children)
        for name, children in ctx.imports.first_party_edges(
            include_type_checking=False
        ).items()
    }
    source = ctx.files.sources.get(caller)
    graph_caller = (
        caller if source is not None and source.path == path else f"<test:{path}>"
    )
    adjacency.setdefault(graph_caller, set()).add(package)
    adjacency.setdefault(package, set()).update(set(targets) - {package})
    nodes = set(adjacency) | {
        child for children in adjacency.values() for child in children
    }
    cycle = shortest_cycle(package, adjacency, nodes) or []
    return {
        "cycle_check": "cycle" if cycle else "no-cycle-found",
        "cycle": cycle,
        "proposed_exports": [] if cycle else exports,
        "blocked_exports": exports if cycle else [],
        "automatic_rewrite": False,
        "initialization_safety": "unproven",
    }
