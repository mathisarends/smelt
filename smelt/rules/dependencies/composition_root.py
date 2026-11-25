from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc


class CompositionRootLeak(Rule):
    activation = POLICIES["ARC006"]
    code = "ARC006"
    name = "architecture/composition-root-leak"
    architecture_scoped = True
    category = Category.ARCHITECTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary="A module imports the composition root or a declared wiring module.",
        rationale=(
            "Wiring belongs in one place. When features reach for the container or the "
            "bootstrap module, construction logic spreads and dependencies become hidden. "
            "With architecture.import_graph.transitive, paths through arbitrary project "
            "modules are checked and reported with a shortest import chain."
        ),
        bad="# voice/application/session.py\nfrom gateway.main import container",
        good=(
            "# voice/application/session.py\n"
            "class StartSession:\n"
            "    def __init__(self, sessions: VoiceSessionRepository) -> None: ..."
        ),
        fix=(
            "Receive dependencies through constructor parameters and register them in the "
            "composition root."
        ),
        canonical=(
            "Receive dependencies as parameters; never import the composition"
            " root or wiring modules"
        ),
        config=(
            "architecture.composition_root",
            "architecture.wiring",
            "architecture.import_graph.transitive",
            "architecture.import_graph.type_checking",
        ),
    )
