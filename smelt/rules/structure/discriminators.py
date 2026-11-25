from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class PreferStrEnumDiscriminator(Rule):
    code = "MOD003"
    name = "modeling/prefer-str-enum-discriminator"
    category = Category.MODELING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Prefer StrEnum members for single-string class discriminator fields.",
        rationale="Discriminated union variants often repeat protocol strings in Literal annotations, field defaults and dispatch code. A shared StrEnum gives those values one named definition while Literal[EventType.MEMBER] preserves each variant's narrow type. This opt-in syntax review recognizes directly annotated class fields named type, kind or tag whose typing.Literal contains exactly one raw string, with or without a default. It supports typing and typing_extensions import aliases, quoted annotations and Annotated/Required/NotRequired wrappers. Dataclasses, model classes and class-based TypedDict declarations use the same syntax. The field shape is a review candidate, not proof of union membership. Other field names, multiple-value Literals, enum-member Literals, ClassVar, type aliases, inherited fields and uncertain or shadowed typing bindings are excluded. Declared Python minima before 3.11 are excluded; unknown minima require version review.",
        bad='from dataclasses import dataclass, field\nfrom typing import Literal\n\n@dataclass\nclass CheckpointExtension:\n    tag: str\n    data: dict[str, object]\n    type: Literal["checkpoint.extension"] = field(default="checkpoint.extension", kw_only=True)',
        good='from dataclasses import dataclass, field\nfrom enum import StrEnum\nfrom typing import Literal\n\nclass EventType(StrEnum):\n    CHECKPOINT_EXTENSION = "checkpoint.extension"\n\n@dataclass\nclass CheckpointExtension:\n    tag: str\n    data: dict[str, object]\n    type: Literal[EventType.CHECKPOINT_EXTENSION] = field(default=EventType.CHECKPOINT_EXTENSION, kw_only=True)',
        fix="Define or reuse a shared StrEnum for the union's wire values. Keep each variant annotated as Literal[EnumType.MEMBER], and use that same member in direct defaults, dataclasses.field(default=...) or model Field(default=...). Keep explicit string values such as checkpoint.extension; auto() may change the wire contract. Review consumers and serialization before changing defaults, since exact str checks and repr can distinguish enum members. StrEnum requires Python 3.11+. No automatic rewrite is performed; analyzed code is never executed. Suppress intentional external schema strings with a reason or disable this review for their package.",
        canonical="Use StrEnum members for string discriminator fields",
        config=("rules", "style.overrides", "project.python_version"),
    )
