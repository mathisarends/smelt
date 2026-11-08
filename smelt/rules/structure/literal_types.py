from __future__ import annotations

import json
from collections import Counter, defaultdict
from itertools import islice
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.literal_types import LiteralMembers, LiteralType, literal_types
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class DuplicateLiteralType(Rule):
    code = "SMT338"
    name = "duplicate-literal-type"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Review repeated multi-value Literal types for a shared type alias.",
        rationale="Repeated Literal value sets can drift when a contract changes. This opt-in review groups identical raw value sets within each analyzed root package, including repetitions across modules. It reports one finding per participating file and value set, with related source locations. Direct typing/typing_extensions imports, import aliases, quoted annotations, nested explicit Literals, type aliases and Annotated's first argument are supported. Annotated metadata is never compared or treated as a type. Order and repeated members do not matter; values retain their Python types. Singleton Literals, enum members, named-alias expansion, uncertain imports, test files and separate root packages are excluded. Same-valued types can represent unrelated concepts, so this is a review hint rather than proof that they should share a name. Package rule overrides and suppressions govern findings at each participating file; other analyzed source files still contribute evidence.",
        bad='from typing import Annotated, Literal\nfrom pydantic import Field\n\ntype ClickButton = Annotated[Literal["left", "right"], Field(description="Mouse button")]\n\ndef click_message(button: Literal["left", "right"]) -> str:\n    return button',
        good='from typing import Annotated, Literal\nfrom pydantic import Field\n\ntype MouseButton = Literal["left", "right"]\ntype ClickButton = Annotated[MouseButton, Field(description="Mouse button")]\n\ndef click_message(button: MouseButton) -> str:\n    return button',
        fix="Define or reuse a shared plain type alias when the occurrences describe the same concept. Keep Field descriptions and constraints on the Annotated wrappers that need them. Choose an owning module that respects architecture boundaries and avoids import cycles. Review Python-version, runtime annotation and schema behavior before changing aliases; use TypeAlias syntax where the supported Python version requires it. Suppress intentional independent contracts with a reason. No automatic rewrite is performed; analyzed code is never executed.",
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        groups: dict[tuple[str, LiteralMembers], dict[str, list[LiteralType]]] = (
            defaultdict(lambda: defaultdict(list))
        )
        for source in sorted(
            ctx.files.sources.values(), key=lambda source: source.path
        ):
            package = source.module.split(".", maxsplit=1)[0]
            for occurrence in literal_types(ctx, source.path):
                groups[package, occurrence.members][source.path].append(occurrence)
        for (package, members), files in sorted(groups.items()):
            count = sum(map(len, files.values()))
            if count < 2:  # noqa: PLR2004 - a duplicate needs at least two occurrences
                continue
            label = f"Literal[{', '.join(value for _, value in members)}]"
            locations = list(
                islice(
                    (
                        {"path": path, "line": site.line, "column": site.column}
                        for path, sites in files.items()
                        for site in sites
                    ),
                    5,
                )
            )
            for path, sites in files.items():
                first = sites[0]
                owner = ctx.files.source_for_path(path)
                ordinals: Counter[str] = Counter()
                edges = []
                for site in sites:
                    edges.append(
                        json.dumps([members, site.scope, ordinals[site.scope]])
                    )
                    ordinals[site.scope] += 1
                yield self.violation(
                    f"consider a shared alias for {label}; {count} occurrences in {package}",
                    path=path,
                    line=first.line,
                    column=first.column,
                    end_line=first.end_line,
                    end_column=first.end_column,
                    source_module=owner.module if owner else None,
                    expected={
                        "scope": package,
                        "literal": label,
                        "members": [list(member) for member in members],
                        "count": count,
                        "locations": locations,
                        "locations_truncated": count > len(locations),
                        "literal_edges": edges,
                        "automatic_rewrite": False,
                    },
                    hint="Reuse or extract a plain type alias for the same concept; retain Annotated/Field metadata on the relevant wrappers.",
                )
