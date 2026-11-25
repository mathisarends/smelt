from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class DuplicateLiteralType(Rule):
    code = "TYP005"
    name = "typing/duplicate-literal-type"
    category = Category.TYPING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Review repeated multi-value Literal types for a shared type alias.",
        rationale="Repeated Literal value sets can drift when a contract changes. This opt-in review groups identical raw value sets within each analyzed root package, including repetitions across modules. It reports one finding per participating file and value set, with related source locations. Direct typing/typing_extensions imports, import aliases, quoted annotations, nested explicit Literals, type aliases and Annotated's first argument are supported. Annotated metadata is never compared or treated as a type. Order and repeated members do not matter; values retain their Python types. Singleton Literals, enum members, named-alias expansion, uncertain imports, test files and separate root packages are excluded. Same-valued types can represent unrelated concepts, so this is a review hint rather than proof that they should share a name. Package rule overrides and suppressions govern findings at each participating file; other analyzed source files still contribute evidence.",
        bad='from typing import Annotated, Literal\nfrom pydantic import Field\n\ntype ClickButton = Annotated[Literal["left", "right"], Field(description="Mouse button")]\n\ndef click_message(button: Literal["left", "right"]) -> str:\n    return button',
        good='from typing import Annotated, Literal\nfrom pydantic import Field\n\ntype MouseButton = Literal["left", "right"]\ntype ClickButton = Annotated[MouseButton, Field(description="Mouse button")]\n\ndef click_message(button: MouseButton) -> str:\n    return button',
        fix="Define or reuse a shared plain type alias when the occurrences describe the same concept. Keep Field descriptions and constraints on the Annotated wrappers that need them. Choose an owning module that respects architecture boundaries and avoids import cycles. Review Python-version, runtime annotation and schema behavior before changing aliases; use TypeAlias syntax where the supported Python version requires it. Suppress intentional independent contracts with a reason. No automatic rewrite is performed; analyzed code is never executed.",
        canonical=(
            "Share one type alias for a repeated multi-value Literal that "
            "names one concept"
        ),
        config=("rules", "style.overrides"),
    )
