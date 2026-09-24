from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import ModuleKind
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.testing.common import is_private

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.syntax import ModuleSyntax


def _references(syntax: ModuleSyntax) -> set[str]:
    names: set[str] = set()
    for node in syntax.walk():
        match node:
            case ast.Name(id=name) | ast.Attribute(attr=name):
                names.add(name)
            case ast.ImportFrom(names=aliases):
                names.update(alias.name for alias in aliases)
            case ast.Assign(targets=[ast.Name(id="__all__")], value=value):
                names.update(
                    item.value
                    for item in ast.walk(value)
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
    return names


_ROUTE_DECORATORS = frozenset(
    {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "options",
        "head",
        "route",
        "api_route",
        "websocket",
    }
)


def _is_registered_route(statement: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in _ROUTE_DECORATORS
        for decorator in statement.decorator_list
    )


class ApiUsedOnlyByTests(BaseRule):
    code = "SMT408"
    name = "test-only-api"
    category = Category.TESTS
    default_severity = Severity.HINT
    requires = frozenset({Index.FILES, Index.SYNTAX})
    doc = RuleDoc(
        summary="A public production function or class is only used by tests.",
        rationale=(
            "Code that exists only for tests widens the public surface and suggests the "
            "tests exercise helpers instead of behavior. It may also be dead code. "
            "Functions registered as HTTP routes are used by the framework."
        ),
        bad="def reset_cache_for_tests() -> None: ...",
        good="Test through the behavior that uses the cache.",
        fix="Remove the symbol, make it private, or test through its real callers.",
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        sources = list(ctx.syntax.sources())
        production: set[str] = set()
        for syntax in sources:
            production |= _references(syntax)
        tested: set[str] = set()
        for syntax in ctx.syntax.tests():
            tested |= _references(syntax)
        model = ctx.model
        for syntax in sources:
            info = model.info(syntax.module)
            if info is not None and info.kind is ModuleKind.COMPOSITION_ROOT:
                continue
            for statement in syntax.tree.body:
                if not isinstance(
                    statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
                ):
                    continue
                name = statement.name
                if is_private(name) or name.startswith("__") or name in production:
                    continue
                if isinstance(
                    statement, ast.FunctionDef | ast.AsyncFunctionDef
                ) and _is_registered_route(statement):
                    continue
                if name not in tested:
                    continue
                yield self.violation(
                    f"{syntax.module}.{name} is only used by tests",
                    path=syntax.path,
                    line=statement.lineno,
                    source_module=syntax.module,
                    feature=info.feature if info else None,
                    layer=info.layer if info else None,
                    hint="Remove it, make it private, or test through its real callers.",
                )
