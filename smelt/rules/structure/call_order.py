from smelt.analysis.context import Index
from smelt.diagnostics.fixes import (
    FixAvailability,
)
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class CallerBeforeCallee(Rule):
    code = "LAY006"
    name = "layout/callee-before-caller"
    category = Category.LAYOUT
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="A direct caller appears after same-visibility helpers it calls.",
        rationale="A step-down reading order introduces a higher-level operation before its lower-level helpers. This opt-in review covers stable undecorated top-level sync/async functions and direct methods on undecorated classes without bases. It resolves direct same-module names and receiver.method calls, excludes shadowed bindings, nested scopes, comprehensions, dunder methods and recursive call components, and respects definition-time parameter/return dependencies. Only same-visibility pairs are reviewed so LAY004's public-first categories retain precedence. Dynamic lookup, inherited APIs and general call graphs are not inferred. This is a reading convention, not a correctness check.",
        bad="def parse():\n    return 1\n\ndef load():\n    return parse()",
        good="def load():\n    return parse()\n\ndef parse():\n    return 1",
        fix="Review placing the caller before its helper within the same public/private group. Preserve defaults, decorators, annotations, rebinding and execution barriers. check --fix moves the caller, with the comment lines directly above it, before its earliest helper; it leaves callers whose defaults or annotations read names bound by the definitions they would pass. LAY004 independently handles public-first ordering and its bounded fixer. Enable rules: {layout/callee-before-caller: hint} or select LAY006; package overrides and reasoned suppressions apply.",
        canonical=(
            "Place a caller before the helpers it calls within the same "
            "visibility group"
        ),
        config=("rules", "style.overrides"),
    )
