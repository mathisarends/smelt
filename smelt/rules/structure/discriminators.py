from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.discriminators import string_discriminators
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class PreferStrEnumDiscriminator(Rule):
    file_local = True
    code = "SMT324"
    name = "prefer-str-enum-discriminator"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Prefer StrEnum members for single-string class discriminator fields.",
        rationale="Discriminated union variants often repeat protocol strings in Literal annotations, field defaults and dispatch code. A shared StrEnum gives those values one named definition while Literal[EventType.MEMBER] preserves each variant's narrow type. This opt-in syntax review recognizes directly annotated class fields named type, kind or tag whose typing.Literal contains exactly one raw string, with or without a default. It supports typing and typing_extensions import aliases, quoted annotations and Annotated/Required/NotRequired wrappers. Dataclasses, model classes and class-based TypedDict declarations use the same syntax. The field shape is a review candidate, not proof of union membership. Other field names, multiple-value Literals, enum-member Literals, ClassVar, type aliases, inherited fields and uncertain or shadowed typing bindings are excluded. Declared Python minima before 3.11 are excluded; unknown minima require version review.",
        bad='from dataclasses import dataclass, field\nfrom typing import Literal\n\n@dataclass\nclass CheckpointExtension:\n    tag: str\n    data: dict[str, object]\n    type: Literal["checkpoint.extension"] = field(default="checkpoint.extension", kw_only=True)',
        good='from dataclasses import dataclass, field\nfrom enum import StrEnum\nfrom typing import Literal\n\nclass EventType(StrEnum):\n    CHECKPOINT_EXTENSION = "checkpoint.extension"\n\n@dataclass\nclass CheckpointExtension:\n    tag: str\n    data: dict[str, object]\n    type: Literal[EventType.CHECKPOINT_EXTENSION] = field(default=EventType.CHECKPOINT_EXTENSION, kw_only=True)',
        fix="Define or reuse a shared StrEnum for the union's wire values. Keep each variant annotated as Literal[EnumType.MEMBER], and use that same member in direct defaults, dataclasses.field(default=...) or model Field(default=...). Keep explicit string values such as checkpoint.extension; auto() may change the wire contract. Review consumers and serialization before changing defaults, since exact str checks and repr can distinguish enum members. StrEnum requires Python 3.11+. No automatic rewrite is performed; analyzed code is never executed. Suppress intentional external schema strings with a reason or disable this review for their package.",
        config=("rules", "structure.contracts", "project.python_version"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            syntax = ctx.syntax.for_path(source.path)
            if syntax is None:
                continue
            target = ctx.python_targets.for_path(source.absolute)
            if target.minimum is not None and target.minimum < (3, 11):
                continue
            for candidate in string_discriminators(syntax.tree):
                node = candidate.node
                yield self.violation(
                    f"consider a StrEnum member for {candidate.owner}.{candidate.field}'s discriminator {candidate.value!r}",
                    path=source.path,
                    line=node.lineno,
                    column=node.col_offset + 1,
                    end_line=node.end_lineno,
                    end_column=(node.end_col_offset or 0) + 1,
                    source_module=source.module,
                    expected={
                        "class": candidate.owner,
                        "field": candidate.field,
                        "value": candidate.value,
                        "python": target.to_json(),
                        "automatic_rewrite": False,
                    },
                    hint="Use Literal[EnumType.MEMBER] with a shared StrEnum; use the same member in the default and preserve the explicit wire string.",
                )
