from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class StatefulDataclass(Rule):
    code = "MOD005"
    name = "modeling/dataclass-field-mutation"
    category = Category.MODELING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A dataclass mutates its own fields after construction.",
        rationale=(
            "A dataclass reads as a value: its generated __init__ is the "
            "constructor and its fields are what callers may rely on. When "
            "methods keep writing to self, the class is really a mutable "
            "accumulator or state machine with a value-like surface: callers "
            "can construct it in a half-built state, observe fields that are "
            "only scratch space for private helpers, and share instances whose "
            "contents change underneath them. Building the values first and "
            "passing them to a frozen dataclass, or keeping the accumulation "
            "in a plain class with private attributes, makes the lifecycle "
            "explicit. This opt-in review reports a @dataclass that is not "
            "frozen=True when a method other than __post_init__ assigns, "
            "deletes or augments an attribute of self, assigns or deletes an "
            "item of a field, calls a mutating container method such as "
            "append, add, update, pop or discard on a field declared as a "
            "mutable container, or calls setattr on self. Mutations inside "
            "nested functions of a method count. Calls that mutate through "
            "other objects, classes that are not dataclasses and tests are "
            "not reported. Intentional mutable records can keep a reasoned "
            "suppression."
        ),
        bad=(
            "@dataclass\n"
            "class InterruptedActivity:\n"
            "    turn: str | None = None\n"
            "    started: set[str] = field(default_factory=set)\n\n"
            "    @classmethod\n"
            "    def scan(cls, events: Iterable[Event]) -> Self:\n"
            "        activity = cls()\n"
            "        for event in events:\n"
            "            activity._observe(event)\n"
            "        return activity\n\n"
            "    def _observe(self, event: Event) -> None:\n"
            "        if isinstance(event, TurnStarted):\n"
            "            self.turn = event.turn_id\n"
            "        elif isinstance(event, ToolStarted):\n"
            "            self.started.add(event.call_id)"
        ),
        good=(
            "@dataclass(frozen=True)\n"
            "class InterruptedActivity:\n"
            "    turn: str | None\n"
            "    started: frozenset[str]\n\n"
            "    @classmethod\n"
            "    def scan(cls, events: Iterable[Event]) -> Self:\n"
            "        turn = None\n"
            "        started: set[str] = set()\n"
            "        for event in events:\n"
            "            if isinstance(event, TurnStarted):\n"
            "                turn = event.turn_id\n"
            "            elif isinstance(event, ToolStarted):\n"
            "                started.add(event.call_id)\n"
            "        return cls(turn=turn, started=frozenset(started))"
        ),
        fix=(
            "Compute the field values first and pass them to the constructor of "
            "a frozen dataclass, using immutable field types such as tuple, "
            "frozenset or Mapping; derive further values with properties or "
            "dataclasses.replace. When the object genuinely accumulates state, "
            "make it a plain class whose scratch attributes are private, or "
            "move the accumulation into a separate builder. No automatic "
            "rewrite is performed. Enable rules: {modeling/dataclass-field-mutation: hint}, "
            "or select MOD005. Package overrides and reasoned inline "
            "suppressions are supported."
        ),
        canonical=(
            "Compute dataclass values before construction; do not mutate "
            "fields afterwards"
        ),
        config=("rules",),
    )
