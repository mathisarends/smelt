from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class DuplicateClassName(Rule):
    code = "MOD009"
    name = "modeling/duplicate-class-name"
    category = Category.MODELING
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Two modules of one package define a public class with the same name.",
        rationale=(
            "When two modules of the same package each define a public class "
            "called PageContent, imports, search results and review comments "
            "stop saying which one is meant, and the wrong import still type "
            "checks when the shapes are similar. This opt-in review groups "
            "public top-level classes by their nearest package, counting a "
            "package's __init__.py as part of that package, and reports every "
            "class whose name is also defined at the top level of another "
            "module there. Re-exports, private classes, nested classes, "
            "classes in separate packages and tests are not compared. Each "
            "definition gets its own finding with the other locations."
        ),
        bad=(
            "# app/observation/models.py\n"
            "class PageContent:\n"
            "    html: str\n\n"
            "# app/observation/content.py\n"
            "class PageContent(Options):\n"
            "    markdown: str"
        ),
        good=(
            "# app/observation/models.py\n"
            "class CapturedPage:\n"
            "    html: str\n\n"
            "# app/observation/content.py\n"
            "class PageRead(Options):\n"
            "    markdown: str"
        ),
        fix=(
            "Rename the classes after their role so each name says which one "
            "is meant, or merge them when they describe the same thing. A "
            "class that only exists as a private implementation detail can "
            "take a leading underscore. No automatic rewrite is performed. "
            "Enable rules: {modeling/duplicate-class-name: warning}, or select MOD009."
        ),
        canonical="Give public classes in one package distinct names",
        config=("rules",),
    )
