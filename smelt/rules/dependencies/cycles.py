from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class ImportCycle(Rule):
    code = "ARC004"
    name = "architecture/import-cycle"
    architecture_scoped = True
    category = Category.ARCHITECTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.IMPORTS})
    doc = RuleDoc(
        summary="Import cycles between architecture nodes or explicitly selected concrete modules.",
        rationale=(
            "Cycles mean two parts can only be understood, tested and changed together. "
            "They also cause import-order bugs at runtime, unless one edge is imported "
            "only under `if TYPE_CHECKING:`; such cycles are marked "
            "`(type checking only)`. Imports that other rules "
            "already forbid (ARC001, ARC002, ARC006) and outbound imports from "
            "declared wiring are left out, so a cycle report means the cycle is built "
            "entirely from ordinary allowed dependencies."
            " Existing features/layers/siblings scopes aggregate nodes and do not prove a connected concrete runtime module cycle. The opt-in modules scope uses exact resolved modules; its witness prefers runtime edges when a runtime cycle exists. witness_edges records each selected edge's runtime or type-checking phase. Runtime here means outside TYPE_CHECKING, including deferred imports; it does not prove eager import-time failure. type_checking_cycles: ignore excludes annotation edges from cycle analysis while other boundary checks can still include them. Defaults and existing finding identities are retained."
        ),
        bad=(
            "# billing/application/invoices.py\n"
            "from gateway.features.voice.application import calls\n"
            "# voice/application/calls.py\n"
            "from gateway.features.billing.application import invoices"
        ),
        good=(
            "# voice/application/calls.py publishes an event;\n"
            "# billing/application/invoices.py subscribes to it."
        ),
        fix=(
            "Break one edge of the cycle: extract the shared piece into a module both can "
            "import, invert one dependency behind an abstraction, or merge the parts."
        ),
        canonical=(
            "Keep imports acyclic: move shared code into a module both sides can import"
        ),
        config=(
            "architecture.import_graph.cycles",
            "architecture.import_graph.type_checking",
            "architecture.import_graph.type_checking_cycles",
        ),
    )
