from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc


class PrivateModuleImport(Rule):
    activation = POLICIES["ARC007"]
    code = "ARC007"
    name = "architecture/non-public-module-import"
    category = Category.ARCHITECTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS, Index.SYNTAX})
    doc = RuleDoc(
        summary="Code outside a contracted package imports a private implementation module.",
        rationale=(
            "Explicit entry points keep callers coupled to a package's supported "
            "interface. Internal imports and intentional symbol re-exports through "
            "public facades remain valid. Wiring and composition roots still obey "
            "these contracts. Discovered tests, including conftest.py and tests "
            "inside the contracted package's configured test roots, are outside "
            "callers and must use the public API. With `test_access: mirrored`, a "
            "test whose tests.mirror path mirrors a module of the package counts as "
            "inside it and may import its private modules; conftest.py and other "
            "tests stay outside. Test-quality exclusions do not "
            "exempt imports; project exclusions and normal suppressions apply. "
            "Layer and feature policies apply independently."
        ),
        bad="# app/client.py\nfrom app.orders.storage import OrderStore",
        good="# app/client.py\nfrom app.orders.api import place_order",
        fix=(
            "Import through one of the contract's public entry points. Expose a "
            "deliberate API there if needed, or explicitly revise the contract. "
            "Public entries are exact module patterns; add .** only when the whole "
            "subtree is intentionally public."
        ),
        canonical="Import a contracted package only through its public entry points",
        config=(
            "architecture.packages",
            "architecture.import_graph.type_checking",
            "project.test_roots",
            "tests.mirror",
        ),
    )
