from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

_CONDITIONAL = (
    ast.IfExp,
    ast.BoolOp,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)
_CALLEE_PREVIEW = 60


class AwaitInCallArgument(Rule):
    file_local = True
    code = "SMT340"
    name = "await-in-call-argument"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="An awaited result is passed directly as a call argument.",
        rationale=(
            "Naming an awaited result before passing it on separates the suspension "
            "point from the construction or call that consumes it, and gives the value "
            "a name in tracebacks and debuggers. This opt-in review reports an await "
            "expression that is a direct positional or keyword argument of a call in "
            "production code, including nested forms such as await f(await g()). "
            "Plain awaits, return await, assignments, awaits nested inside containers "
            "or other expressions and calls without await, such as asyncio.gather(f(), "
            "g()), are not reported. Awaits in comprehensions, lambdas, conditional "
            "expressions, boolean operators and while conditions are skipped because "
            "extracting them would change when or how often they run. Tests are not "
            "checked. Extracting an argument evaluates it before the arguments to its "
            "left; review order-sensitive calls."
        ),
        bad=(
            "async def read(browser: Browser) -> ClipboardResult:\n"
            "    return ClipboardResult(text=await browser.clipboard.read())"
        ),
        good=(
            "async def read(browser: Browser) -> ClipboardResult:\n"
            "    text = await browser.clipboard.read()\n"
            "    return ClipboardResult(text=text)"
        ),
        fix=(
            "Assign the awaited result to a local variable in the statement before "
            "the call and pass that variable. For a keyword argument the keyword is "
            "suggested as the variable name. No automatic rewrite is performed. "
            "Enable rules: {await-in-call-argument: hint}, or select SMT340. Package "
            "overrides and reasoned inline suppressions are supported."
        ),
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            for call, awaited, keyword in _awaited_arguments(tree, conditional=False):
                callee = ast.unparse(call.func)
                if len(callee) > _CALLEE_PREVIEW:
                    callee = callee[: _CALLEE_PREVIEW - 3] + "..."
                variable = keyword if keyword and keyword.isidentifier() else None
                hint = (
                    f"Assign `{variable} = {ast.unparse(awaited)}` before the call "
                    f"and pass `{variable}={variable}`."
                    if variable
                    else "Assign the awaited result to a named local variable "
                    "before the call and pass that variable."
                )
                yield self.violation(
                    f"name the awaited result before passing it to {callee}()",
                    path=source.path,
                    line=awaited.lineno,
                    column=awaited.col_offset + 1,
                    end_line=awaited.end_lineno,
                    end_column=(awaited.end_col_offset or awaited.col_offset) + 1,
                    source_module=source.module,
                    expected={
                        "call": callee,
                        "argument": keyword,
                        "awaited": ast.unparse(awaited.value),
                        "suggested_variable": variable,
                        "automatic_rewrite": False,
                    },
                    hint=hint,
                )


def _awaited_arguments(
    node: ast.AST, *, conditional: bool
) -> Iterator[tuple[ast.Call, ast.Await, str | None]]:
    if isinstance(node, ast.Call) and not conditional:
        for argument in node.args:
            if isinstance(argument, ast.Await):
                yield node, argument, None
        for keyword in node.keywords:
            if isinstance(keyword.value, ast.Await) and keyword.arg is not None:
                yield node, keyword.value, keyword.arg
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            nested = False
        else:
            nested = (
                conditional
                or isinstance(node, _CONDITIONAL)
                or (isinstance(node, ast.While) and child is node.test)
            )
        yield from _awaited_arguments(child, conditional=nested)
