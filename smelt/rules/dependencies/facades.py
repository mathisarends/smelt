from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc


class FacadeExportGap(Rule):
    activation = POLICIES["ARC008"]
    code = "ARC008"
    name = "architecture/missing-facade-export"
    category = Category.ARCHITECTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.IMPORTS, Index.SYNTAX})
    doc = RuleDoc(
        summary="A public implementation name has many outside importers but no package export.",
        rationale=(
            "A contract's min_importers_to_export threshold counts distinct analyzed modules outside "
            "the defining package, aggregating aliases and static re-export chains. "
            "One advisory finding per symbol and facade is anchored at the definition. "
            "Tests count only with count_tests: true; TYPE_CHECKING-only imports never "
            "count. Private names, module imports, namespace packages, uncertain bindings "
            "and recognized dynamic facade exports are skipped. Existing package exports and "
            "explicit __all__ re-exports in other modules are "
            "context, not coverage for this facade. Frequency suggests an API review, "
            "not a requirement to expand the public API. Counts cover analyzed code only."
            " Optional architecture.canonical_exports maps a qualified definition or static alias to its supported public export. Smelt verifies both against the same static identity, including supported literal lazy-export tables; missing, conflicting or unsupported dynamic owners are configuration errors. A verified owner prevents duplicate export advice for that symbol only. Importer context separates same-root and other-root production consumers. Ownership never relaxes layers, package-public contracts or cycle checks; without declarations existing thresholds behave as before."
        ),
        bad=(
            "# app/browser/__init__.py is empty\n"
            "# Three outside modules each use:\n"
            "from app.browser.base import BrowserEvents"
        ),
        good=(
            "# After reviewing ownership and initialization behavior:\n"
            "# app/browser/__init__.py\nfrom .base import BrowserEvents\n"
            "__all__ = ['BrowserEvents']\n"
            "# callers\nfrom app.browser import BrowserEvents"
        ),
        fix=(
            "Review whether the name belongs in the package API or whether callers "
            "should move. The finding lists importers and other public export paths. "
            "A cycle witness means adding the facade dependency closes a runtime "
            "import path; no-cycle-found means only that no return path was found in "
            "the analyzed explicit module graph, not that initialization is safe. "
            "Dynamic imports, implicit package initialization and import order are "
            "not proven. No exports, imports or policy are rewritten automatically."
        ),
        canonical=(
            "Export implementation names that many outside modules import "
            "from their package"
        ),
        config=("architecture.packages", "architecture.canonical_exports"),
    )
