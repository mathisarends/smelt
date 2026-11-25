from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc


class RedundantDishkaProvider(Rule):
    code = "FWK002"
    name = "frameworks/redundant-dishka-provider"
    category = Category.FRAMEWORKS
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A Dishka provider method only forwards typed dependencies to a constructor.",
        rationale="Dishka can register constructors directly using provide(Implementation, provides=Interface). This opt-in review recognizes direct, undecorated Provider subclasses and synchronous @provide methods containing only one return of a class-shaped constructor call. Every typed dependency must be forwarded once, with matching keyword names or unchanged positional order. Stable direct Dishka import aliases are supported. Defaults, variadic signatures, extra decorators, mixins, async/generator/resource factories, transformations and unknown decorator options are excluded. Constructor annotations and dependency markers are not proven equivalent; the finding is a review candidate and never a graph validation or resource-ownership guarantee.",
        bad="from dishka import Provider, Scope, provide\n\nclass AdminProvider(Provider):\n    scope = Scope.REQUEST\n    @provide\n    def query(self, session: AsyncSession) -> AdminUserQuery:\n        return SqlAdminUserQuery(session)",
        good="from dishka import Provider, Scope, provide\n\nclass AdminProvider(Provider):\n    scope = Scope.REQUEST\n    query = provide(SqlAdminUserQuery, provides=AdminUserQuery)",
        fix="Review registering the constructor directly. Verify its type hints and Dishka dependency/component markers supply the same inputs. Preserve class-level scope and explicit scope/cache/provides/override options. Keep factories with initialization logic or finalization. Suggested registrations use the canonical provide spelling; preserve the actual import alias when editing. No automatic rewrite is performed. Enable rules: {frameworks/redundant-dishka-provider: hint}.",
        canonical=(
            "Register a Dishka constructor directly instead of a provider "
            "method that only forwards to it"
        ),
        config=("rules", "style.overrides"),
    )
