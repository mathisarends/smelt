from __future__ import annotations

import ast
import hashlib
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

_DECISIONS = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.TryStar,
    ast.Match,
    ast.Raise,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.IfExp,
)


class ConstructorLogic(Rule):
    file_local = True
    activation = POLICIES["SMT364"]
    code = "SMT364"
    name = "complexity/constructor-logic"
    category = Category.COMPLEXITY
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A constructor contains more decisions than the configured limit.",
        rationale=(
            "Normalization, validation and derivation become easier to understand "
            "when named separately from object initialization. This opt-in review "
            "counts if, for/async for, while, try/try-star, match, raise, comprehension "
            "and conditional-expression nodes in __init__ and __post_init__ methods. "
            "Each comprehension counts once, in addition to any conditional expressions "
            "inside it; its generator clauses are not counted separately. Nested "
            "function, lambda and class definitions are skipped. Helper calls count "
            "zero. A single guard with raise counts two, so it remains valid at the "
            "default limit of 2. Only production source is checked. __new__ and "
            "other validation methods are outside the scope. This metric is advice, "
            "not proof that a constructor needs refactoring."
        ),
        bad=(
            "class Client:\n"
            "    def __init__(self, values):\n"
            "        for value in values:\n"
            "            if not value.ready:\n"
            "                raise ValueError('not ready')\n"
            "        self.values = values"
        ),
        good=(
            "class Client:\n"
            "    def __init__(self, values):\n"
            "        self.values = validate_values(values)"
        ),
        fix=(
            "Move a cohesive normalization or validation step into a named function "
            "or classmethod and assign its result. Preserve evaluation order, exceptions "
            "and initialization side effects. Do not extract arbitrary fragments merely "
            "to lower a count. No automatic rewrite is performed. Enable rules: "
            "{complexity/constructor-logic: hint}, select SMT364, or set "
            "max_constructor_decisions under structure or in a package contract. "
            "The limit defaults to 2; 0 forbids any counted decision. Reasoned "
            "suppressions and package off overrides are supported."
        ),
        config=("rules", "structure.max_constructor_decisions", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            limit, _ = ctx.contracts.threshold(
                source.module, "max_constructor_decisions"
            )
            for owner in ast.walk(ctx.asts.parse(source.path)):
                if not isinstance(owner, ast.ClassDef):
                    continue
                for method in owner.body:
                    if not isinstance(
                        method, ast.FunctionDef | ast.AsyncFunctionDef
                    ) or method.name not in {"__init__", "__post_init__"}:
                        continue
                    decisions = sorted(
                        (
                            node
                            for statement in method.body
                            for node in _decisions(statement)
                        ),
                        key=lambda node: (node.lineno, node.col_offset),
                    )
                    if len(decisions) <= limit:
                        continue
                    yield self.violation(
                        f"Constructor {owner.name}.{method.name} contains {len(decisions)} decisions (limit {limit}); name its normalization or validation steps",
                        path=source.path,
                        line=method.lineno,
                        column=method.col_offset + 1,
                        source_module=source.module,
                        expected={
                            "class": owner.name,
                            "method": method.name,
                            "max_constructor_decisions": limit,
                            "count": len(decisions),
                            "decision_edges": [
                                f"{owner.name}.{method.name}:{hashlib.sha256(ast.dump(node).encode()).hexdigest()}"
                                for node in decisions
                            ],
                            "decisions": [
                                {
                                    "kind": type(node).__name__,
                                    "line": node.lineno,
                                    "column": node.col_offset + 1,
                                }
                                for node in decisions
                            ],
                            "automatic_rewrite": False,
                        },
                        hint="Move a cohesive step into a named helper and assign its result; preserve initialization order and exceptions.",
                    )


def _decisions(node: ast.AST) -> Iterator[ast.stmt | ast.expr]:
    if isinstance(
        node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef
    ):
        return
    if isinstance(node, _DECISIONS):
        yield node
    for child in ast.iter_child_nodes(node):
        yield from _decisions(child)
