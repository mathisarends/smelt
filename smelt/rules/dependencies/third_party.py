from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc


class ThirdPartyDenied(Rule):
    activation = POLICIES["ARC003"]
    code = "ARC003"
    name = "architecture/forbidden-third-party-import"
    architecture_scoped = True
    category = Category.ARCHITECTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary="A layer imports a third-party package its `third_party` policy does not allow.",
        rationale=(
            "Inner layers should not know about frameworks, drivers or SDKs. Keeping them "
            "out makes the core portable and cheap to test. With "
            "architecture.import_graph.transitive, the importing layer's policy also "
            "applies to packages reached through project modules, such as "
            "domain -> shared.utils -> sqlalchemy. A shortest chain includes each "
            "import's source line. External packages are leaves: their own source "
            "is not scanned. Standard-library imports remain allowed."
        ),
        bad="# voice/domain/session.py\nfrom sqlalchemy.orm import Mapped",
        good="# voice/domain/session.py\nfrom dataclasses import dataclass",
        fix=(
            "Move the code that needs the package into a layer that allows it and expose "
            "it through a port, or extend the layer's `third_party` policy."
        ),
        canonical=(
            "Import a third-party package only in layers whose third_party "
            "policy allows it"
        ),
        config=(
            "architecture.layers.<layer>.third_party.default",
            "architecture.layers.<layer>.third_party.allow",
            "architecture.layers.<layer>.third_party.deny",
            "architecture.import_graph.transitive",
            "architecture.import_graph.type_checking",
        ),
    )
