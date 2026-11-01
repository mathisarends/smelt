"""Conservative evidence that all recognized checks can vanish on empty output."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.literals import nonempty_literal
from smelt.analysis.test_helpers import outcome_propagates

if TYPE_CHECKING:
    from smelt.analysis.test_syntax import TestFunction


def vacuous_outcomes(
    test: TestFunction, helpers: list[str]
) -> list[ast.For | ast.AsyncFor | ast.Call]:
    """Report only when every recognized outcome is loop/all dependent.

    An additional explicit check is evidence, not a proof of a nonempty collection.
    No attempt is made to judge persistence semantics or general reachability.
    """
    return _vacuous_evidence(test, helpers, test.outcome_evidence(helpers))


def helper_outcome_review(
    test: TestFunction, helpers: list[str]
) -> list[dict[str, object]]:
    """Explain existing inference boundaries without following additional bodies."""
    parents = {
        child: parent
        for parent in ast.walk(test.node)
        for child in ast.iter_child_nodes(parent)
    }
    calls = {call.node: call.qualified for call in test.calls}
    followed = {helper.node: helper for helper in test.helpers}
    review: list[dict[str, object]] = []
    for call in test.calls:
        helper = followed.get(call.node)
        if helper is not None:
            reason = "no-recognized-evidence-within-one-hop"
            if helper.function.outcome_evidence(helpers) and not helper.outcomes(
                helpers
            ):
                reason = "helper-context-may-swallow-evidence"
            elif helper.outcomes(helpers) and not outcome_propagates(
                call.node, parents, calls
            ):
                reason = "caller-context-may-swallow-evidence"
        elif call.helper_line is not None:
            reason = "local-coroutine-not-recognized-as-executed"
        elif call.provenance == "import":
            reason = "imported-body-not-analyzed"
        else:
            continue
        review.append(
            {"call": call.qualified, "line": call.node.lineno, "reason": reason}
        )
    return review


def _vacuous_evidence(
    test: TestFunction, helpers: list[str], evidence: list[ast.Assert | ast.Call]
) -> list[ast.For | ast.AsyncFor | ast.Call]:
    if not evidence:
        return []
    parents = {
        child: node
        for node in ast.walk(test.node)
        for child in ast.iter_child_nodes(node)
    }
    universal = {call.node for call in test.calls if call.qualified == "builtins.all"}
    executions = {helper.node: helper for helper in test.helpers}
    risks: list[ast.For | ast.AsyncFor | ast.Call] = []
    for outcome in evidence:
        child: ast.AST = outcome
        parent = parents.get(outcome)
        risky: ast.For | ast.AsyncFor | ast.Call | None = None
        while parent is not None and parent is not test.node:
            if (
                isinstance(parent, ast.For | ast.AsyncFor)
                and child in parent.body
                and _possibly_empty(parent.iter, test.nonempty_iterables)
            ):
                risky = parent
                break
            child, parent = parent, parents.get(parent)
        if risky is None and isinstance(outcome, ast.Assert):
            expression = outcome.test
            # Explicit empty/alternative cases and compound assertions remain
            # outside this narrow review aid.
            if (
                isinstance(expression, ast.Call)
                and expression in universal
                and expression.args
                and _possibly_empty(expression.args[0], test.nonempty_iterables)
            ):
                risky = expression
        if risky is None and isinstance(outcome, ast.Call) and outcome in executions:
            execution = executions[outcome]
            helper_risks = _vacuous_evidence(
                execution.function, helpers, execution.outcomes(helpers)
            )
            if helper_risks:
                risky = helper_risks[0]
        if risky is None:
            return []
        if risky not in risks:
            risks.append(risky)
    return risks


def _possibly_empty(node: ast.expr, known: set[ast.expr]) -> bool:
    if isinstance(node, ast.GeneratorExp | ast.ListComp | ast.SetComp | ast.DictComp):
        return any(
            generator.ifs
            or generator.is_async
            or _possibly_empty(generator.iter, known)
            for generator in node.generators
        )
    return node not in known and not nonempty_literal(node)
