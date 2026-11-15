from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import SourceFile

type _Function = ast.FunctionDef | ast.AsyncFunctionDef

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
# Fewer copies are an ordinary local narrowing.
_MIN_METHODS = 2


@dataclass(frozen=True, slots=True)
class _Site:
    method: str
    node: ast.Assert


class DuplicateNarrowingAssert(Rule):
    code = "SMT356"
    name = "duplicate-narrowing-assert"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Several methods of a class assert the same narrowing of a self attribute.",
        rationale=(
            "assert self._stack is not None in one method narrows a type for "
            "the checker. When several methods of the class repeat it, the "
            "attribute is optional in its type but required after setup, and "
            "every caller depends on a check that python -O removes. This "
            "opt-in review reports, once per class and expression, an assert "
            "of self.<attribute> is not None or isinstance(self.<attribute>, "
            "...) that appears in at least two methods of the same class, "
            "including each operand of an and. Asserts in nested functions "
            "belong to that function, static methods are skipped and tests "
            "are not checked. Unlike a blanket assert ban, single local "
            "narrowings stay valid."
        ),
        bad=(
            "class Tabs:\n"
            "    def open(self, url: str) -> None:\n"
            "        assert self._stack is not None\n"
            "        self._stack.push(url)\n\n"
            "    def close(self) -> None:\n"
            "        assert self._stack is not None\n"
            "        self._stack.pop()"
        ),
        good=(
            "class Tabs:\n"
            "    @property\n"
            "    def stack(self) -> Stack:\n"
            "        if self._stack is None:\n"
            '            raise RuntimeError("Tabs used before start()")\n'
            "        return self._stack\n\n"
            "    def open(self, url: str) -> None:\n"
            "        self.stack.push(url)"
        ),
        fix=(
            "Make the attribute required by constructing it before the object "
            "is used, or read it through one property or accessor that raises "
            "an error naming the missing setup step. No automatic rewrite is "
            "performed. Enable rules: {duplicate-narrowing-assert: warning}, or "
            "select SMT356."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            syntax = ctx.syntax.for_path(source.path)
            if syntax is None:
                continue
            for owner in ast.walk(syntax.tree):
                if isinstance(owner, ast.ClassDef):
                    yield from self._class(source, owner)

    def _class(self, source: SourceFile, owner: ast.ClassDef) -> Iterator[Violation]:
        # asserted expression -> sites, in source order
        sites: dict[str, list[_Site]] = defaultdict(list)
        for method in owner.body:
            if not isinstance(method, _FUNCTIONS):
                continue
            receiver = _receiver(method)
            if receiver is None:
                continue
            for node in _asserts(method):
                for expression in _narrowings(node.test, receiver):
                    sites[expression].append(_Site(method.name, node))
        for expression, found in sites.items():
            methods = list(dict.fromkeys(site.method for site in found))
            if len(methods) < _MIN_METHODS:
                continue
            first = found[0].node
            yield self.violation(
                f"assert {expression} repeats in {len(methods)} methods of "
                f"{owner.name}: {', '.join(methods)}",
                path=source.path,
                line=first.lineno,
                column=first.col_offset + 1,
                source_module=source.module,
                expected={
                    "class": owner.name,
                    "expression": expression,
                    "methods": methods,
                    "lines": [site.node.lineno for site in found],
                    "automatic_rewrite": False,
                },
                hint=(
                    "Make the attribute required during construction, or read "
                    "it through one property that raises an error naming the "
                    "missing setup."
                ),
            )


def _receiver(method: _Function) -> str | None:
    if any(
        isinstance(decorator, ast.Name) and decorator.id == "staticmethod"
        for decorator in method.decorator_list
    ):
        return None
    arguments = [*method.args.posonlyargs, *method.args.args]
    return arguments[0].arg if arguments else None


def _asserts(method: _Function) -> Iterator[ast.Assert]:
    pending: list[ast.AST] = list(method.body)
    while pending:
        node = pending.pop()
        if isinstance(node, _SCOPES):
            continue
        if isinstance(node, ast.Assert):
            yield node
        pending.extend(ast.iter_child_nodes(node))


def _narrowings(test: ast.expr, receiver: str) -> Iterator[str]:
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        for value in test.values:
            yield from _narrowings(value, receiver)
        return
    if _is_not_none(test, receiver) or _is_instance(test, receiver):
        yield ast.unparse(test)


def _is_not_none(test: ast.expr, receiver: str) -> bool:
    return (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.IsNot)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
        and _on_receiver(test.left, receiver)
    )


def _is_instance(test: ast.expr, receiver: str) -> bool:
    return (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Name)
        and test.func.id == "isinstance"
        and bool(test.args)
        and _on_receiver(test.args[0], receiver)
    )


def _on_receiver(node: ast.expr, receiver: str) -> bool:
    if not isinstance(node, ast.Attribute):
        return False
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name) and node.id == receiver
