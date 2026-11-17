from __future__ import annotations

import ast
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

type _Function = ast.FunctionDef | ast.AsyncFunctionDef

# Smaller bodies are too generic to be worth a shared module.
_MIN_STATEMENTS = 3
_MAX_LOCATIONS = 5


class DuplicateFunction(Rule):
    code = "SMT352"
    name = "complexity/duplicate-function"
    category = Category.COMPLEXITY
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A module-level function repeats another function's code.",
        rationale=(
            "Copy-pasted helpers drift: a fix or a changed message lands in one "
            "copy and the others keep the old behavior, and readers cannot tell "
            "whether the difference is intended. This opt-in review compares "
            "module-level functions within each analyzed root package and "
            "reports every function whose parameters, annotations, decorators "
            "and body are identical to another one's, ignoring names, "
            "docstrings, comments and formatting. A body needs at least three "
            "statements, counting nested ones, so trivial one-liners are not "
            "reported. Methods, nested functions, test files and separate root "
            "packages are not compared. Each copy gets its own finding with "
            "the other locations."
        ),
        bad=(
            "# app/tools/keyboard.py\n"
            "def _secret_entered(exact: bool) -> Effect:\n"
            "    if exact:\n"
            '        return Effect("Text entered.")\n'
            '    return Effect("Text entered with a slip.", break_chain=True)\n\n'
            "# app/tools/typing.py\n"
            "def _secret_entered(exact: bool) -> Effect:\n"
            "    if exact:\n"
            '        return Effect("Text entered.")\n'
            '    return Effect("Text entered with a slip.", break_chain=True)'
        ),
        good=(
            "# app/tools/effects.py\n"
            "def secret_entered(exact: bool) -> Effect:\n"
            "    if exact:\n"
            '        return Effect("Text entered.")\n'
            '    return Effect("Text entered with a slip.", break_chain=True)\n\n'
            "# app/tools/keyboard.py and app/tools/typing.py\n"
            "from app.tools.effects import secret_entered"
        ),
        fix=(
            "Keep one copy in a module both callers may import, usually in "
            "their nearest common package, and import it from there. Check "
            "architecture boundaries and import cycles when choosing the "
            "owner. Copies that must evolve independently keep a reasoned "
            "suppression. No automatic rewrite is performed. Enable rules: "
            "{complexity/duplicate-function: warning}, or select SMT352."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        # root package -> body fingerprint -> copies
        groups: dict[str, dict[str, list[_Copy]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for source in sorted(ctx.files.sources.values(), key=lambda item: item.path):
            package = source.module.split(".", maxsplit=1)[0]
            for function in ctx.asts.parse(source.path).body:
                if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if (fingerprint := _fingerprint(function)) is not None:
                    groups[package][fingerprint].append(
                        _Copy(source.path, source.module, function)
                    )
        for package in sorted(groups):
            for fingerprint, copies in sorted(groups[package].items()):
                if len(copies) >= 2:  # noqa: PLR2004 - a duplicate needs two copies
                    yield from self._report(fingerprint, copies)

    def _report(self, fingerprint: str, copies: list[_Copy]) -> Iterator[Violation]:
        owner = _common_package([copy.module for copy in copies])
        for copy in copies:
            others = [other for other in copies if other is not copy]
            first = others[0]
            yield self.violation(
                f"Function {copy.node.name} repeats {first.node.name} in "
                f"{first.path}"
                + (f" and {len(others) - 1} more" if len(others) > 1 else ""),
                path=copy.path,
                line=copy.node.lineno,
                column=copy.node.col_offset + 1,
                source_module=copy.module,
                expected={
                    "function": copy.node.name,
                    "fingerprint": fingerprint,
                    "count": len(copies),
                    "copies": [
                        {
                            "path": other.path,
                            "line": other.node.lineno,
                            "function": other.node.name,
                        }
                        for other in others[:_MAX_LOCATIONS]
                    ],
                    "copies_truncated": len(others) > _MAX_LOCATIONS,
                    "common_package": owner,
                    "automatic_rewrite": False,
                },
                hint=(
                    f"Keep one copy in a shared module under {owner} and "
                    "import it from every caller."
                ),
            )


@dataclass(frozen=True, slots=True)
class _Copy:
    path: str
    module: str
    node: _Function


def _fingerprint(function: _Function) -> str | None:
    body = function.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    statements = sum(
        isinstance(node, ast.stmt) for statement in body for node in ast.walk(statement)
    )
    if statements < _MIN_STATEMENTS:
        return None
    # Names, docstrings and positions do not change what the function does.
    shape = type(function)(
        name="",
        args=function.args,
        body=body,
        decorator_list=function.decorator_list,
        returns=function.returns,
        type_params=function.type_params,
    )
    return hashlib.sha256(ast.dump(shape).encode()).hexdigest()[:16]


def _common_package(modules: list[str]) -> str:
    parts = [module.split(".")[:-1] or module.split(".") for module in modules]
    common: list[str] = []
    for segments in zip(*parts, strict=False):
        if len(set(segments)) != 1:
            break
        common.append(segments[0])
    return ".".join(common) or parts[0][0]
