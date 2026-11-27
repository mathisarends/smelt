from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class RedundantGetattr(Rule):
    code = "IDM009"
    name = "idioms/redundant-getattr"
    category = Category.IDIOMS
    default_severity = Severity.HINT
    requires = frozenset({Index.FILES, Index.SYNTAX})
    doc = RuleDoc(
        summary="A literal getattr or hasattr reads an attribute that the receiver's declared project type always has.",
        rationale=(
            "getattr(obj, 'name', default) and hasattr(obj, 'name') suggest that the "
            "attribute can be missing and hide the access from the type checker. When "
            "every project class of the receiver's declared type declares it, the plain "
            "attribute read behaves the same and stays checked. The opt-in hint checks "
            "production modules. Receiver types come from parameter annotations, "
            "unannotated method receivers, locals assigned once from an annotation, a "
            "project class constructor or an annotated project function, loop targets "
            "over annotated collections, annotated instance attributes and properties, "
            "and type aliases of project class unions. An attribute counts as declared "
            "by a class-body assignment, method, property or nested class, an annotation "
            "in a dataclass-like class with a decorator or base, an unconditional "
            "receiver assignment at the top of __init__, or a project base that declares "
            "it. An annotation without a value in __init__ does not create an attribute. "
            "Receivers typed Any, object, a type parameter, an outside class, an "
            "Optional or a union member without the attribute stay quiet, as do dunder "
            "and name-mangled private names, TypedDicts, classes with __getattr__, "
            "__getattribute__ or __slots__ "
            "in their project chain, and receivers inside construction methods such as "
            "__init__. Attributes added by setattr, monkeypatching or metaclasses are "
            "outside its inference."
        ),
        bad=(
            "def last_activity(entries: tuple[UserMessage | ToolCall, ...]) -> datetime:\n"
            "    latest = datetime.min\n"
            "    for entry in entries:\n"
            '        created_at = getattr(entry, "created_at", None)\n'
            "        if isinstance(created_at, datetime) and created_at > latest:\n"
            "            latest = created_at\n"
            "    return latest"
        ),
        good=(
            "def last_activity(entries: tuple[UserMessage | ToolCall, ...]) -> datetime:\n"
            "    latest = datetime.min\n"
            "    for entry in entries:\n"
            "        if entry.created_at > latest:\n"
            "            latest = entry.created_at\n"
            "    return latest"
        ),
        fix=(
            "Read the attribute directly, and replace hasattr checks by the branch they "
            "guarded. Then review checks on the value, such as an isinstance test or a "
            "None default, that the declared type already settles. Keep the reflection "
            "when the attribute is meant to be optional, and declare it optional in the "
            "class instead. No automatic rewrite is performed."
        ),
        canonical="Read attributes the declared type guarantees directly instead of through getattr or hasattr",
        config=("rules",),
        smell="getattr or hasattr with a literal name reads an attribute that every declared type of the receiver always has.",
        no_smell="An attribute that is meant to be optional: declare it optional on the class and keep the reflection.",
        repair="Read the attribute directly and replace a hasattr check by the branch it guarded; then drop checks on the value that the declared type settles.",
        subject="{call}({receiver}, '{attribute}')",
    )
