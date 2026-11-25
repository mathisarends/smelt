from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc


class MissingRequiredModule(Rule):
    activation = POLICIES["ARC014"]
    code = "ARC014"
    name = "architecture/missing-required-module"
    category = Category.ARCHITECTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A package is missing a required module or public entry point.",
        rationale=(
            "Explicit package APIs let contributors and agents follow the same "
            "structure. They apply only to matching packages and require no particular "
            "framework, feature layout or layer names. Excluded code does not fulfill "
            "a contract. This checks presence, not implementation quality."
        ),
        bad="architecture:\n  packages:\n    slices:\n      match: app.slices.*\n      required: [api]   # app.slices.orders.api is absent",
        good="architecture:\n  packages:\n    slices:\n      match: app.slices.*\n      required: [api]   # every matching package has an api module or package",
        fix=(
            "Implement the missing module in the matching package. If that package "
            "should follow a different structure, narrow or split the contract. Do "
            "not add empty implementation stubs solely to satisfy the check."
        ),
        canonical=(
            "Provide the modules and public entry points the package contract requires"
        ),
        config=("architecture.packages",),
        example_language="yaml",
    )
