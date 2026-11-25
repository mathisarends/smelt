from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc


class DeepObjectNavigation(Rule):
    activation = POLICIES["CPX001"]
    code = "CPX001"
    name = "complexity/excess-attribute-depth"
    category = Category.COMPLEXITY
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Review a method call reached through multiple instance attributes.",
        rationale=(
            "An instance method that reaches through several stored collaborators depends on their internal arrangement. "
            "This opt-in Law of Demeter review reports calls with at least two consecutive attribute accesses from the "
            "instance receiver before the first method call, such as self.browser.input.motor(). One finding covers "
            "a whole receiver call chain. Direct collaborator calls, pure data reads and fluent chains starting from "
            "a direct collaborator are excluded. Module objects stored from unshadowed imports or declared as ModuleType, "
            "explicit SimpleNamespace trees, and typed dataclass/Pydantic record paths ending in known containers or "
            "values are namespaces rather than collaborator graphs. Known pathlib parent paths and subprocess pipes "
            "also retain their standard APIs. Namespace evidence comes from annotations, direct constructor assignments "
            "and property return annotations; analyzed imports and re-exports are followed without executing code. "
            "Every intermediate object must be a known namespace or value: a record containing a service does not "
            "exempt navigation into that service. Names such as api, client, get or append alone exempt nothing. "
            "Unknown SDK types and dynamic property/data flow remain review limitations unless the navigation "
            "option declares namespace_types or instance_factories for them. Decorated and implicit "
            "class/static methods, nested helper bodies, comprehensions, receiver rebinding and navigation through "
            "parameters, aliases or subscripts are outside the review. This is a bounded review, not proof of a design defect."
            " When namespace evidence is incomplete, the finding includes the declared/constructed root type and "
            "the unresolved step. These are review inputs for an agent, not proof that the API is poorly designed. "
            "Inspect the public API contract before choosing a refactor; an intentional namespace can be a valid conclusion."
        ),
        bad="class Controller:\n    def aim(self, width, height):\n        return self.browser.input.motor().aim(width, height)",
        good="class Controller:\n    def __init__(self, motor):\n        self.motor = motor\n\n    def aim(self, width, height):\n        return self.motor.aim(width, height)",
        fix="Review whether the caller should receive the needed collaborator directly or ask its direct collaborator to perform the operation. A local variable alone does not reduce structural coupling. Preserve factory/property evaluation and object lifetimes; repeated lookups need not return the same object. Keep intentional API navigation with a package rules override set to off or a reasoned CPX001 suppression. No automatic rewrite is performed. Enable with rules: {complexity/excess-attribute-depth: hint}, globally or in a package override, or --select CPX001.",
        canonical=(
            "Call methods on direct collaborators, not through chains of "
            "instance attributes"
        ),
        config=("rules", "style.navigation", "style.overrides"),
    )
