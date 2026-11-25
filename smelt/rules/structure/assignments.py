from smelt.analysis.context import Index
from smelt.diagnostics.fixes import FixAvailability
from smelt.diagnostics.violation import Category
from smelt.rules.base import Rule, RuleDoc


class BundledInstanceAssignment(Rule):
    code = "IDM001"
    name = "idioms/bundled-instance-assignment"
    category = Category.IDIOMS
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="An assignment writes multiple direct instance attributes under a separate-assignment convention.",
        rationale="Separate field assignments make object initialization easy to scan and extend. This opt-in package convention reports tuple/list unpacking and chained assignments that write multiple direct attributes of a method's first positional parameter, regardless of its name. It leaves local unpacking and single-field assignments alone. Recognized static/class methods and nested helper bodies are excluded; nested classes are inspected with their own receiver. This is a readability policy, not a logic error.",
        bad="class Inbox:\n    def __init__(self, log, control):\n        self.log, self.control = log, control",
        good="class Inbox:\n    def __init__(self, log, control):\n        self.log = log\n        self.control = control",
        fix="Use one direct instance attribute write per statement. Preserve evaluation timing by unpacking into locals before assigning fields when appropriate. check --fix --unsafe-fixes can split chained writes of a name or constant and same-length tuple or list unpacking into direct fields. Review the change: splitting can repeat a name lookup or write a field before a later value is evaluated.",
        canonical="Assign one instance attribute per statement",
        config=("rules", "style.overrides"),
    )
