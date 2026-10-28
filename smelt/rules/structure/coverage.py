from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class IncompleteCoverage(BaseRule):
    code = "SMT306"
    name = "incomplete-coverage"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="Strict coverage found source outside the architecture policy or an exclusion without a reason.",
        rationale=(
            "A passing architecture check is only useful when production code cannot "
            "silently escape its package selection or classification. Strict coverage "
            "inventories source roots and Python workspace members independently of "
            "project.root_packages. Pure package facades do not need their own layer."
        ),
        bad=(
            "# smelt.yaml\nproject:\n  root_packages: [backend]\n  coverage: strict\n"
            "# A new workspace library exists but is not selected or classified."
        ),
        good=(
            "# smelt.yaml\nproject:\n  root_packages: [backend, client]\n"
            "  source_roots: [backend/src, libs/client/src]\n  coverage: strict\n"
            "architecture:\n  modules: {client: infrastructure}"
        ),
        fix=(
            "Select missing production packages and assign their architecture role. "
            "Move unclassified implementation into an existing layer. Deliberately "
            "excluded code needs project.exclude entries with path and reason. "
            "Coverage gaps cannot be hidden by debt, inline ignores or a narrowed check."
        ),
        config=(
            "project.coverage",
            "project.root_packages",
            "project.source_roots",
            "project.exclude",
            "architecture.modules",
        ),
        example_language="yaml",
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        if ctx.config.project.coverage != "strict":
            return
        for pattern in ctx.coverage.unexplained_exclusions:
            yield self.violation(
                f'exclusion "{pattern}" has no coverage reason',
                expected={
                    "exclusion": {
                        "path": pattern,
                        "reason": "<why this code is outside this policy>",
                    }
                },
                hint="Use a project.exclude entry with path and a nonempty reason.",
            )
        for entry in ctx.coverage.gaps:
            package = entry.module.split(".")[0] if entry.module else None
            standalone = bool(
                entry.module
                and "." not in entry.module
                and not entry.path.endswith("__init__.py")
            )
            hint = (
                f"Select {package or 'the package'} under project.root_packages and "
                f"{entry.source_root or 'its import root'} under project.source_roots, then declare its architecture role."
                if entry.status == "uncovered"
                else "Move the implementation into a declared layer, or assign its intended "
                "role under architecture.modules. Reserve wiring for actual assembly."
            )
            if entry.status == "uncovered" and standalone:
                package = None
                hint = "Move the standalone module into a governed package; project.root_packages selects directories, not individual Python files."
            yield self.violation(
                f"{entry.path} is {entry.status}: {entry.reason}",
                path=entry.path,
                source_module=entry.module,
                feature=entry.feature,
                layer=entry.layer,
                expected={
                    "coverage_status": "governed, facade or excluded with a reason",
                    "actual_status": entry.status,
                    "root_package": package,
                    "source_root": entry.source_root,
                },
                hint=hint
                + " For deliberate exclusions, declare path and reason under project.exclude.",
            )
