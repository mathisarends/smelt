from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class PackageOnlyDunderAll(Rule):
    code = "VIS003"
    name = "visibility/misplaced-dunder-all"
    category = Category.VISIBILITY
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="An ordinary module declares __all__ outside a package initializer.",
        rationale="This opt-in convention puts deliberate export lists in __init__.py so a package has one visible public facade. It reports module-scope __all__ bindings, including guarded, annotated, augmented and imported bindings. Function/class-local names and test files are outside scope. Package initializers may declare __all__; LAY004 independently checks definition order. This is an API convention, not a claim that Python forbids ordinary module exports.",
        bad='\n# app/events.py\ndef open_events(): ...\n__all__ = ["open_events"]',
        good='\n# app/events.py\ndef open_events(): ...\n\n# app/__init__.py\nfrom .events import open_events\n__all__ = ["open_events"]',
        fix="Review the package API and move the deliberate export list to __init__.py, preserving intended re-exports and checking cycles. Removing __all__ can change star imports and the visibility evidence used by other rules. No list is moved or removed automatically. Enable rules: {visibility/misplaced-dunder-all: error}; closest package overrides can retain compatibility modules.",
        canonical="Declare __all__ only in package __init__.py files",
        config=("rules", "style.overrides"),
    )


class ReexportOnlyModule(Rule):
    code = "LAY010"
    name = "layout/reexport-only-module"
    category = Category.LAYOUT
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="An ordinary module consists entirely of explicit re-exports.",
        rationale=(
            "A module that only forwards names adds another import path to maintain. "
            "This opt-in hint looks for direct imports marked by same-name aliases "
            "or a single literal __all__ list or tuple covering every import binding. "
            "A module docstring and future imports are allowed. The check is limited "
            "to ordinary production modules with distinct, unambiguous bindings. "
            "Package initializers, star imports, rebinding, conditional imports and "
            "dynamic export declarations fall outside this source pattern."
        ),
        bad=(
            "# app/events_api.py\nfrom .events import Event as Event\n\n"
            "# app/client.py\nfrom .events_api import Event"
        ),
        good="# app/client.py\nfrom .events import Event",
        fix=(
            "Review moving callers to the defining module or an intentional package "
            "API. A supported compatibility path can use a reasoned suppression. "
            "Check published import paths, side effects and cycles before removing "
            "a forwarding module. This is a read-only review."
        ),
        canonical=(
            "Import from the defining module or the package API; do not add "
            "re-export-only modules"
        ),
        config=("rules", "style.overrides"),
    )
