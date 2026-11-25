from smelt.analysis.context import Index
from smelt.diagnostics.fixes import FixAvailability
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc


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
