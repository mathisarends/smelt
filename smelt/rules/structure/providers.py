from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.providers import provider_forwardings
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class RedundantDishkaProvider(Rule):
    file_local = True
    code = "SMT328"
    name = "redundant-dishka-provider"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A Dishka provider method only forwards typed dependencies to a constructor.",
        rationale="Dishka can register constructors directly using provide(Implementation, provides=Interface). This opt-in review recognizes direct, undecorated Provider subclasses and synchronous @provide methods containing only one return of a class-shaped constructor call. Every typed dependency must be forwarded once, with matching keyword names or unchanged positional order. Stable direct Dishka import aliases are supported. Defaults, variadic signatures, extra decorators, mixins, async/generator/resource factories, transformations and unknown decorator options are excluded. Constructor annotations and dependency markers are not proven equivalent; the finding is a review candidate and never a graph validation or resource-ownership guarantee.",
        bad="from dishka import Provider, Scope, provide\n\nclass AdminProvider(Provider):\n    scope = Scope.REQUEST\n    @provide\n    def query(self, session: AsyncSession) -> AdminUserQuery:\n        return SqlAdminUserQuery(session)",
        good="from dishka import Provider, Scope, provide\n\nclass AdminProvider(Provider):\n    scope = Scope.REQUEST\n    query = provide(SqlAdminUserQuery, provides=AdminUserQuery)",
        fix="Review registering the constructor directly. Verify its type hints and Dishka dependency/component markers supply the same inputs. Preserve class-level scope and explicit scope/cache/provides/override options. Keep factories with initialization logic or finalization. Suggested registrations use the canonical provide spelling; preserve the actual import alias when editing. No automatic rewrite is performed. Enable rules: {redundant-dishka-provider: hint}.",
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            for candidate in provider_forwardings(ctx.asts.parse(source.path)):
                yield self.violation(
                    f"review direct constructor registration for {candidate.owner}.{candidate.node.name}",
                    path=source.path,
                    line=candidate.node.lineno,
                    column=candidate.node.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "class": candidate.owner,
                        "method": candidate.node.name,
                        "constructor": candidate.constructor,
                        "dependencies": list(candidate.dependencies),
                        "suggested_registration": candidate.registration,
                        "automatic_rewrite": False,
                    },
                    hint=f"Review {candidate.node.name} = {candidate.registration}; verify constructor hints and preserve scope/cache/dependency markers.",
                )
