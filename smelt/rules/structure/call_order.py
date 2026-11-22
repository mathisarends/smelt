from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.call_order import call_order_issues
from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import tree_nodes
from smelt.diagnostics.fixes import Applicability, Fix, FixAvailability, TextEdit
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.edits import SourceText

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.call_order import CallOrderIssue


class CallerBeforeCallee(Rule):
    file_local = True
    code = "LAY006"
    name = "layout/callee-before-caller"
    category = Category.LAYOUT
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="A direct caller appears after same-visibility helpers it calls.",
        rationale="A step-down reading order introduces a higher-level operation before its lower-level helpers. This opt-in review covers stable undecorated top-level sync/async functions and direct methods on undecorated classes without bases. It resolves direct same-module names and receiver.method calls, excludes shadowed bindings, nested scopes, comprehensions, dunder methods and recursive call components, and respects definition-time parameter/return dependencies. Only same-visibility pairs are reviewed so LAY004's public-first categories retain precedence. Dynamic lookup, inherited APIs and general call graphs are not inferred. This is a reading convention, not a correctness check.",
        bad="def parse():\n    return 1\n\ndef load():\n    return parse()",
        good="def load():\n    return parse()\n\ndef parse():\n    return 1",
        fix="Review placing the caller before its helper within the same public/private group. Preserve defaults, decorators, annotations, rebinding and execution barriers. check --fix moves the caller, with the comment lines directly above it, before its earliest helper; it leaves callers whose defaults or annotations read names bound by the definitions they would pass. LAY004 independently handles public-first ordering and its bounded fixer. Enable rules: {layout/callee-before-caller: hint} or select LAY006; package overrides and reasoned suppressions apply.",
        canonical=(
            "Place a caller before the helpers it calls within the same "
            "visibility group"
        ),
        config=("rules", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            text: SourceText | None = None
            for issue in call_order_issues(tree):
                text = text or SourceText(ctx.files.read_text(source.path))
                fix = _fix(text, tree, issue)
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
                        "automatic_rewrite": fix is not None,
                    },
                    hint="Keep the high-level caller above same-visibility helpers after reviewing definition-time dependencies and execution boundaries.",
                    fix=fix,
                )


def _fix(source: SourceText, tree: ast.Module, issue: CallOrderIssue) -> Fix | None:
    """Move the caller, with its leading comments, above its earliest helper.

    Safe: an undecorated definition only evaluates its defaults and
    annotations, and the move is refused when code it passes binds their names.
    """
    body = _scope_body(tree, issue.scope)
    caller = issue.node
    if body is None or not source.stands_alone(caller):
        return None
    first = min(line for _, line in issue.callees)
    target = next(
        (
            statement
            for statement in body
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
            and statement.lineno == first
        ),
        None,
    )
    if target is None:
        return None
    passed = body[body.index(target) : body.index(caller)]
    if _eager_names(caller) & _bound_names(passed):
        return None
    start = source.block_start(target)
    caller_start = source.block_start(caller)
    caller_end = source.line_end(caller.end_lineno or caller.lineno)
    gap_start = caller_start
    while gap_start > 0:
        previous = source.line_start(source.line_at(gap_start - 1))
        if source.text[previous:gap_start].strip():
            break
        gap_start = previous
    if gap_start <= start:
        return None
    block = source.text[caller_start:caller_end]
    if not block.endswith("\n"):
        block += "\n"
    gap = source.text[gap_start:caller_start]
    return Fix(
        Applicability.SAFE,
        f"Move {caller.name} above the helpers it calls",
        (
            source.insert(start, block + gap),
            TextEdit.at(source.text, (gap_start, caller_end), ""),
        ),
    )


def _scope_body(tree: ast.Module, scope: str) -> list[ast.stmt] | None:
    if scope == "module":
        return tree.body
    return next(
        (
            node.body
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == scope
        ),
        None,
    )


def _eager_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Names a def statement reads when it runs: defaults and annotations."""
    arguments = function.args
    roots: list[ast.expr] = [
        *arguments.defaults,
        *(value for value in arguments.kw_defaults if value is not None),
        *(
            arg.annotation
            for arg in [
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
                *([arguments.vararg] if arguments.vararg else []),
                *([arguments.kwarg] if arguments.kwarg else []),
            ]
            if arg.annotation is not None
        ),
        *([function.returns] if function.returns else []),
    ]
    return {
        node.id
        for root in roots
        for node in tree_nodes(root)
        if isinstance(node, ast.Name)
    }


def _bound_names(statements: list[ast.stmt]) -> set[str]:
    """Every name the statements could bind, including nested definitions."""
    names: set[str] = set()
    for statement in statements:
        for node in tree_nodes(statement):
            match node:
                case ast.Name(id=name, ctx=ast.Store() | ast.Del()):
                    names.add(name)
                case (
                    ast.FunctionDef(name=name)
                    | ast.AsyncFunctionDef(name=name)
                    | ast.ClassDef(name=name)
                ):
                    names.add(name)
                case ast.Import(names=aliases) | ast.ImportFrom(names=aliases):
                    names.update(
                        alias.asname or alias.name.split(".")[0] for alias in aliases
                    )
                case ast.Global(names=declared) | ast.Nonlocal(names=declared):
                    names.update(declared)
                case _:
                    pass
    return names
