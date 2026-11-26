from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class DeepPackageReexport(Rule):
    code = "VIS011"
    name = "visibility/deep-package-reexport"
    category = Category.VISIBILITY
    default_severity = Severity.WARNING
    requires = frozenset({Index.FILES, Index.SYNTAX})
    doc = RuleDoc(
        summary="A package initializer re-exports a name through a deeper import path.",
        rationale=(
            "When a package re-exports a name from a descendant module, every intervening "
            "package should expose it through its initializer. Each step in the export "
            "chain crosses one package boundary, so a subpackage can move its "
            "implementation without changing the parent initializer. The opt-in rule "
            "checks unconditional relative and absolute from-imports in production "
            "__init__.py files when the source "
            "module and the immediate subpackage initializer are in the project. "
            "Static __all__ identifies exports when present. Otherwise, imported "
            "bindings without a leading underscore count as public, including "
            "same-name aliases. Guarded or rebound bindings, star imports and "
            "dynamic export lists are outside its inference."
        ),
        bad=(
            "# app/__init__.py\n"
            "from .runtime.hooks import ExecuteTool\n\n"
            '__all__ = ["ExecuteTool"]'
        ),
        good=(
            "# app/runtime/__init__.py\n"
            "from .hooks import ExecuteTool\n\n"
            '__all__ = ["ExecuteTool"]\n\n'
            "# app/__init__.py\n"
            "from .runtime import ExecuteTool\n\n"
            '__all__ = ["ExecuteTool"]'
        ),
        fix=(
            "Expose the imported names from the immediate subpackage, then import them "
            "from that package in the parent initializer. Apply the same pattern at "
            "each deeper package boundary. Preserve the parent's public aliases and "
            "review name collisions and import cycles before editing."
        ),
        canonical="Re-export through the immediate subpackage's initializer",
        config=("rules",),
    )
