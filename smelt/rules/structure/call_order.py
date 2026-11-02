from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.call_order import call_order_issues
from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class CallerBeforeCallee(Rule):
    file_local = True
    code = "SMT329"
    name = "caller-before-callee"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A direct caller appears after same-visibility helpers it calls.",
        rationale="A step-down reading order introduces a higher-level operation before its lower-level helpers. This opt-in review covers stable undecorated top-level sync/async functions and direct methods on undecorated classes without bases. It resolves direct same-module names and receiver.method calls, excludes shadowed bindings, nested scopes, comprehensions, dunder methods and recursive call components, and respects definition-time parameter/return dependencies. Only same-visibility pairs are reviewed so SMT313's public-first categories retain precedence. Dynamic lookup, inherited APIs and general call graphs are not inferred. This is a reading convention, not a correctness check.",
        bad="def parse():\n    return 1\n\ndef load():\n    return parse()",
        good="def load():\n    return parse()\n\ndef parse():\n    return 1",
        fix="Review placing the caller before its helper within the same public/private group. Preserve defaults, decorators, annotations, rebinding and execution barriers. No definitions are reordered automatically; SMT313 independently handles public-first ordering and its bounded fixer. Enable rules: {caller-before-callee: hint} or select SMT329; package overrides and reasoned suppressions apply.",
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            for issue in call_order_issues(ctx.asts.parse(source.path)):
                yield self.violation(
                    f"review placing {issue.node.name} before the helpers it calls",
                    path=source.path,
                    line=issue.node.lineno,
                    column=issue.node.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "scope": issue.scope,
                        "caller": issue.node.name,
                        "callees": [
                            {"name": name, "line": line} for name, line in issue.callees
                        ],
                        "automatic_rewrite": False,
                    },
                    hint="Keep the high-level caller above same-visibility helpers after reviewing definition-time dependencies and execution boundaries.",
                )
