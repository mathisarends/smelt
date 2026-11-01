from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.module_symbols import internal_module_symbols
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.member_names import private_name

if TYPE_CHECKING:
    from collections.abc import Iterator


class InternalPublicModuleSymbol(Rule):
    code = "SMT320"
    name = "internal-public-module-symbol"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX, Index.MEMBERS})
    doc = RuleDoc(
        summary="A public module constant or type alias has no observed external use or public-signature evidence.",
        rationale="Public-looking module data can advertise implementation details. This opt-in review covers single, unconditional uppercase assignments and PEP 695 type aliases in implementation modules. Imports, analyzed re-exports, test accesses, escaped modules, static __all__ exports and public signatures preserve names. Alias dependencies in public annotations are followed, including quoted annotations. Package initializers, rebound or shadowed names, star imports, dynamic exports and detected namespace reflection are excluded. Ordinary assignment-style type aliases and class visibility are outside this check. Excluded files, unselected packages and external or arbitrary dynamic consumers remain unassessed; findings expose usage_scope and do not prove a rename is safe.",
        bad="MIN_LENGTH = 8\ntype Payload = str | bytes\n\ndef _valid(value: Payload):\n    return len(value) >= MIN_LENGTH",
        good="_MIN_LENGTH = 8\ntype _Payload = str | bytes\n\ndef _valid(value: _Payload):\n    return len(value) >= _MIN_LENGTH",
        fix="Review the intended API and callers. Make genuine implementation details private, updating references and quoted annotations. Preserve supported public names with __all__ or a reasoned suppression. A type alias used in a public signature is API evidence, not a private-name candidate; this rule does not require exporting every annotation dependency. No automatic rename is performed.",
        config=("rules", "structure.contracts", "project.test_roots"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for symbol in internal_module_symbols(ctx.syntax, ctx.member_usage):
            name = symbol.node.id
            suggested = private_name(name, symbol.occupied_names)
            yield self.violation(
                f"{name} is public, but has no observed use outside its module or public-signature evidence",
                path=symbol.path,
                line=symbol.node.lineno,
                column=symbol.node.col_offset + 1,
                source_module=symbol.module,
                target_module=f"{symbol.module}.{name}",
                expected={
                    "kind": symbol.kind,
                    "member": name,
                    "suggested_name": suggested,
                    "internal_references": list(symbol.internal_references),
                    "usage_scope": ctx.member_usage.usage_scope,
                    "automatic_rename": False,
                },
                hint=f"Review making {name} private as {suggested}; retain deliberate public APIs with exports or a reasoned suppression.",
            )
