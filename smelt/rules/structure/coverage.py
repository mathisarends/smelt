from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class IncompleteCoverage(Rule):
    scope = "project"
    code = "ARC013"
    name = "architecture/uncovered-source"
    category = Category.ARCHITECTURE
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
            "# smelt.yml\nproject:\n  root_packages: [backend]\nrules:\n  architecture/uncovered-source: error\n"
            "# A new workspace library exists but is not selected or classified."
        ),
        good=(
            "# smelt.yml\nproject:\n  root_packages: [backend, client]\n"
            "  source_roots: [backend/src, libs/client/src]\n"
            "rules:\n  architecture/uncovered-source: error\n"
            "architecture:\n  layers:\n    infrastructure: {modules: [client]}"
        ),
        fix=(
            "Select missing production packages and assign their architecture role. "
            "Move unclassified implementation into an existing layer. Deliberately "
            "excluded code needs project.exclude entries with path and reason. "
            "Coverage gaps cannot be hidden by debt, inline ignores or a narrowed check."
        ),
        canonical=(
            "Keep new source inside the architecture policy, or exclude it "
            "with a reason"
        ),
        config=(
            "project.root_packages",
            "project.source_roots",
            "project.exclude",
            "architecture.layers.<layer>.modules",
        ),
        example_language="yaml",
    )
