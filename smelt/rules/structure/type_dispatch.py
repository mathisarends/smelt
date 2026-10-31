from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.type_dispatch import type_dispatches
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class PreferMatchTypeDispatch(BaseRule):
    file_local = True
    activation = POLICIES["SMT315"]
    code = "SMT315"
    name = "prefer-match-type-dispatch"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Review a same-subject isinstance/elif chain for match/case under an explicit package policy.",
        rationale="Long type-dispatch chains can be clearer as class patterns and guards. This opt-in hint recognizes positive builtin isinstance tests over the same simple name, optionally followed by and guards. It preserves independent adjacent if statements and reports one candidate per whole supported chain. It is a readability suggestion, not proof of equivalent class-pattern behavior.",
        bad="if isinstance(event, Started):\n    start(event)\nelif isinstance(event, Delta | Progress):\n    update(event)\nelif isinstance(event, Finished) and event.ok:\n    finish(event)",
        good="match event:\n    case Started():\n        start(event)\n    case Delta() | Progress():\n        update(event)\n    case Finished() if event.ok:\n        finish(event)",
        fix="Review class patterns, guards and subclass ordering. Preserve independent if statements, and keep nested body conditions inside their case: moving them into a case guard can introduce fallthrough. Match evaluates its subject once; custom instance checks, dynamic type expressions and side effects may differ. No automatic rewrite is performed. Declared Python minima before 3.10 are excluded; unknown minima require version review.",
        config=("structure.contracts", "project.python_version"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            contract = ctx.contracts.policy_for(source.module, "type_dispatch")
            if (
                contract is None
                or contract.spec.type_dispatch is None
                or contract.spec.type_dispatch.style != "match"
            ):
                continue
            policy = contract.spec.type_dispatch
            target = ctx.python_targets.for_path(source.absolute)
            if target.minimum is not None and target.minimum < (3, 10):
                continue
            for candidate in type_dispatches(
                ctx.asts.parse(source.path), policy.min_branches
            ):
                counts: Counter[str] = Counter()
                edges: list[str] = []
                for branch in candidate.branches:
                    key = f"{candidate.scope}:{candidate.subject}:{branch.types}:{branch.guard}"
                    edges.append(f"{key}#{counts[key]}")
                    counts[key] += 1
                yield self.violation(
                    f"consider match {candidate.subject}: for {len(candidate.branches)} type-dispatch branches",
                    path=source.path,
                    line=candidate.node.lineno,
                    column=candidate.node.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "contract": contract.name,
                        "type_dispatch": policy.model_dump(mode="json"),
                        "subject": candidate.subject,
                        "scope": candidate.scope,
                        "dispatch_edges": edges,
                        "branches": [
                            {
                                "line": branch.line,
                                "types": list(branch.types),
                                "guard": branch.guard,
                            }
                            for branch in candidate.branches
                        ],
                        "python": target.to_json(),
                        "automatic_rewrite": False,
                    },
                    hint=f"Review match {candidate.subject}: with class patterns and guards; preserve separate if statements and nested branch conditions.",
                )
