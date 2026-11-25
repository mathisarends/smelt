from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class AnonymousTupleType(Rule):
    code = "TYP007"
    name = "typing/anonymous-tuple-type"
    category = Category.TYPING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A fixed-length tuple type leaves its fields unnamed.",
        rationale=(
            "dict[UUID, tuple[UUID, UUID, BrowserConnection]] does not say which "
            "UUID is which; readers decode entry[0] and entry[1] from call sites, "
            "and swapping them still type-checks. This opt-in review reports "
            "fixed-length tuple types in annotations and type aliases with three "
            "or more elements, or with an element type that repeats. Variadic "
            "tuple[X, ...], empty tuples, two different element types, quoted "
            "annotations, dunder/@override/@overload signatures (dictated "
            "elsewhere) and tests are not reported."
        ),
        bad=(
            "class Connections:\n"
            "    def __init__(self) -> None:\n"
            "        self._by_tab: dict[UUID, tuple[UUID, UUID, Connection]] = {}"
        ),
        good=(
            "class Route(NamedTuple):\n"
            "    user_id: UUID\n"
            "    run_id: UUID\n"
            "    connection: Connection\n\n"
            "class Connections:\n"
            "    def __init__(self) -> None:\n"
            "        self._by_tab: dict[UUID, Route] = {}"
        ),
        fix=(
            "Introduce a NamedTuple (keeps unpacking and indexing) or a frozen "
            "dataclass that names each field, and use it in place of the tuple. "
            "Keep a tuple whose shape an external API dictates with a reasoned "
            "suppression. No automatic rewrite is performed. Enable rules: "
            "{typing/anonymous-tuple-type: hint}, or select TYP007."
        ),
        canonical=(
            "Name the fields of fixed-length tuples with a NamedTuple or "
            "frozen dataclass"
        ),
        config=("rules",),
    )
