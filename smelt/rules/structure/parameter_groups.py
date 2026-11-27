from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class DuplicateParameterGroup(Rule):
    code = "CPX009"
    name = "complexity/duplicate-parameter-group"
    category = Category.COMPLEXITY
    default_severity = Severity.HINT
    requires = frozenset({Index.FILES, Index.SYNTAX})
    doc = RuleDoc(
        summary="A group of parameter names travels together through several functions.",
        rationale=(
            "When the same values are passed together through several functions, each "
            "caller repeats the whole list and adding a field means editing every "
            "signature. The values usually form a concept that wants a name. The opt-in "
            "hint compares the named parameters of production functions and methods, "
            "without self, cls, *args and **kwargs, and reports a group of at least 5 "
            "names shared by at least 3 functions once, at its first definition, with "
            "every location. The functions must belong to at least 2 override families. "
            "Methods with the same name form one family, which covers overrides, "
            "Protocol and ABC implementations and signatures that an outside base class "
            "dictates. A function that passes at least 5 of its parameters on to "
            "self.<method>, super().<method>, <ProjectClass>.<method>, a project "
            "function, a project class or cls() constructor joins that callee's family, "
            "so forwarding wrappers, factories and DI providers that mirror a "
            "constructor stay quiet. @overload stubs, tests and "
            "excluded files are ignored. Matching on names cannot prove that the values "
            "mean the same thing; the thresholds and the family requirement keep "
            "unrelated collisions rare. The diagnostic lists annotations that all "
            "locations share."
        ),
        bad=(
            "def listen(event_name, event_type, max_queue_size, overflow, timeout_seconds): ...\n"
            "def listen_all(event_name, event_type, max_queue_size, overflow, timeout_seconds): ...\n"
            "def subscribe(event_name, event_type, max_queue_size, overflow, timeout_seconds): ..."
        ),
        good=(
            "@dataclass(frozen=True, slots=True)\n"
            "class Subscription:\n"
            "    event_name: str\n"
            "    event_type: type[Event]\n"
            "    max_queue_size: int\n"
            "    overflow: Overflow\n"
            "    timeout_seconds: float\n\n"
            "def listen(subscription: Subscription): ...\n"
            "def listen_all(subscription: Subscription): ...\n"
            "def subscribe(subscription: Subscription): ..."
        ),
        fix=(
            "Review the locations together. If the names describe one concept, introduce "
            "a value object such as a frozen dataclass and pass it instead of the "
            "separate parameters, starting at the outermost caller. Keep separate "
            "parameters when the values only share names. No automatic rewrite is "
            "performed."
        ),
        canonical="Pass a group of values that travel together as one named value object",
        config=("rules",),
        smell="The same five or more parameters are passed together through several unrelated functions.",
        no_smell="Parameters that only share their names and do not form one concept; keep them separate with a reasoned suppression.",
        repair="Name the concept as one value object, such as a frozen dataclass, and pass it instead, starting at the outermost caller.",
    )
