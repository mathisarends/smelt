from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class InternalPublicModuleSymbol(Rule):
    code = "VIS002"
    name = "visibility/internal-public-module-symbol"
    category = Category.VISIBILITY
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX, Index.MEMBERS, Index.MODEL})
    doc = RuleDoc(
        summary="A public module constant or type alias has no observed external use or public-signature evidence.",
        rationale="Public-looking module data can advertise implementation details. This opt-in review covers single, unconditional uppercase assignments and PEP 695 type aliases in implementation modules. Imports, analyzed re-exports, test accesses, escaped modules, static __all__ exports and public signatures preserve names. Alias dependencies in public annotations are followed, including quoted annotations. Package initializers, rebound or shadowed names, star imports, dynamic exports and detected namespace reflection are excluded. Declared composition roots can expose entry points to a runtime and are skipped. Ordinary assignment-style type aliases and class visibility are outside this check. Excluded files, unselected packages and external or arbitrary dynamic consumers remain unassessed; findings expose usage_scope and do not prove a rename is safe.",
        bad="MIN_LENGTH = 8\ntype Payload = str | bytes\n\ndef _valid(value: Payload):\n    return len(value) >= MIN_LENGTH",
        good="_MIN_LENGTH = 8\ntype _Payload = str | bytes\n\ndef _valid(value: _Payload):\n    return len(value) >= _MIN_LENGTH",
        fix="Review the intended API and callers. Make genuine implementation details private, updating references and quoted annotations. Preserve supported public names with __all__ or a reasoned suppression. A type alias used in a public signature is API evidence, not a private-name candidate; this rule does not require exporting every annotation dependency. No automatic rename is performed.",
        canonical=(
            "Make module constants and type aliases used only inside their "
            "module private"
        ),
        config=(
            "rules",
            "style.overrides",
            "project.test_roots",
            "architecture.composition_root",
        ),
    )
