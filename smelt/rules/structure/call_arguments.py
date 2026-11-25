from __future__ import annotations

import ast

from smelt.analysis.context import AnalysisContext, Index
from smelt.config.models import POLICY_DEFAULTS
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc
from smelt.rules.common import readable_list

type _EagerComprehension = ast.ListComp | ast.SetComp | ast.DictComp
type _ExtractableArgument = _EagerComprehension | ast.Call

_KIND_NAMES = {
    "await": "awaited results",
    "comprehension": "comprehensions",
    "aggregate": "builtin reductions",
}


class InlineCallArgument(Rule):
    activation = POLICIES["CPX002"]
    code = "CPX002"
    name = "complexity/inline-call-argument"
    category = Category.COMPLEXITY
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary=(
            "An awaited result, eager comprehension or builtin reduction is passed "
            "directly as a call argument."
        ),
        rationale=(
            "Naming an intermediate value before passing it on separates the "
            "suspension point or collection transformation from the construction "
            "or call that consumes it, and gives the value a name in tracebacks and "
            "debuggers. This opt-in syntax review reports a direct positional or "
            "named keyword argument of a call in production code that is one of "
            "the kinds in call_argument_kinds (all by default): await "
            "(an await expression, including nested forms such as "
            "await f(await g())), comprehension (a list, set or dict "
            "comprehension, even when CPX006 accepts its internal logic) or "
            "aggregate (an unshadowed builtin all, any, max, min, sorted or sum "
            "reducing a generator expression; the whole reduction is named and the "
            "generator stays lazy). It applies to any call without inferring "
            "constructors or runtime types and reports each argument once. Plain "
            "awaits, return await, named values, other calls and attribute reads, "
            "generator expressions passed directly, starred arguments and values "
            "nested inside another argument expression are outside scope. "
            "Arguments within lambdas, comprehensions, conditional expressions, "
            "boolean operators, while conditions, chained comparisons and assert "
            "messages are skipped because extraction would change when or how "
            "often they run. Tests are excluded."
        ),
        bad=(
            "async def to_response(browser, result):\n"
            "    return Response(\n"
            "        text=await browser.clipboard.read(),\n"
            "        users=[to_user(user) for user in result.users],\n"
            "        active=sum(user.active for user in result.users),\n"
            "    )"
        ),
        good=(
            "async def to_response(browser, result):\n"
            "    text = await browser.clipboard.read()\n"
            "    users = [to_user(user) for user in result.users]\n"
            "    active = sum(user.active for user in result.users)\n"
            "    return Response(text=text, users=users, active=active)"
        ),
        fix=(
            "Assign the value to a local variable in the statement before the call "
            "and pass that variable; a named keyword supplies a suggested variable "
            "name. Preserve the callable lookup, argument evaluation order, guards "
            "and suspension points: moving an argument before the call can "
            "evaluate it before earlier arguments or the callable itself. Do not "
            "eagerly materialize generator expressions. No automatic rewrite is "
            "performed. Enable rules: {complexity/inline-call-argument: hint}, "
            "select CPX002, or set call_argument_kinds under style or in a "
            "style override, which also narrows the review to that scope. "
            "Reasoned inline suppressions are supported."
        ),
        canonical=(
            "Bind awaited results, comprehensions and builtin reductions to a"
            " local name before passing them as arguments"
        ),
        config=("rules", "style.call_argument_kinds", "style.overrides"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        kinds = _argument_kinds(ctx, target.module)
        names = (name for kind, name in _KIND_NAMES.items() if kind in kinds)
        return (
            f"Bind {readable_list(names)} to a local name "
            "before passing them as arguments"
        )


def _argument_kinds(ctx: AnalysisContext, module: str) -> frozenset[str]:
    contract = ctx.contracts.policy_for(module, "call_argument_kinds")
    return frozenset(
        contract.spec.call_argument_kinds
        if contract and contract.spec.call_argument_kinds
        else POLICY_DEFAULTS.call_argument_kinds
    )
