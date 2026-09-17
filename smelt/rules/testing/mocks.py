from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import ModuleKind
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.code.common import node_violation
from smelt.rules.testing.common import (
    categories,
    dotted,
    is_mock_factory,
    mock_spec,
    patch_call,
    test_functions,
    visible_fixtures,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.syntax import ModuleSyntax
    from smelt.rules.testing.common import FunctionNode

_INTERACTION_METHODS = frozenset(
    {
        "assert_called",
        "assert_called_once",
        "assert_called_with",
        "assert_called_once_with",
        "assert_any_call",
        "assert_has_calls",
        "assert_not_called",
        "assert_awaited",
        "assert_awaited_once",
        "assert_awaited_with",
        "assert_awaited_once_with",
        "assert_any_await",
        "assert_has_awaits",
        "assert_not_awaited",
    }
)
_INTERACTION_ATTRIBUTES = frozenset(
    {
        "call_count",
        "call_args",
        "call_args_list",
        "await_count",
        "await_args",
        "mock_calls",
    }
)
_BOUNDARY_CATEGORIES = frozenset({"external", "stdlib", "environment"})


class MocksFirstParty(BaseRule):
    code = "SMT404"
    name = "mocks-first-party"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.FILES, Index.SYNTAX})
    doc = RuleDoc(
        summary="A test mocks a first-party class from a layer in mocks.forbid_first_party.",
        rationale=(
            "A mock of your own port accepts any call sequence. A small fake that "
            "implements the port behaves like the real thing and survives refactorings."
        ),
        bad="sessions = create_autospec(VoiceSessionRepository)",
        good=(
            "class FakeSessions(VoiceSessionRepository):\n"
            "    def __init__(self) -> None:\n"
            "        self.saved: list[VoiceSession] = []"
        ),
        fix="Write a fake implementing the port and assert on its state.",
        config=("tests.mocks.forbid_first_party",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        forbid = set(ctx.config.tests.mocks.forbid_first_party)
        if not forbid:
            return
        for syntax in ctx.syntax.tests():
            for node in syntax.walk():
                if not isinstance(node, ast.Call) or not is_mock_factory(node):
                    continue
                spec = mock_spec(node)
                qualname = ctx.syntax.resolve(syntax, spec) if spec else None
                cls = ctx.syntax.class_info(qualname)
                if cls is None:
                    continue
                info = ctx.model.info(cls.module)
                group = None
                if info is not None and info.layer in forbid:
                    group = info.layer
                elif (
                    info is not None
                    and info.kind is ModuleKind.SHARED
                    and "shared" in forbid
                ):
                    group = "shared"
                if group is None:
                    continue
                yield node_violation(
                    self,
                    ctx,
                    syntax,
                    node,
                    f"mocks {cls.name} from {group}; use a fake implementing it",
                    target_module=cls.module,
                    expected={"forbid_first_party": sorted(forbid)},
                    hint=f"Write a small fake {cls.name} and assert on its state.",
                )


def count_mocks(
    syntax: ModuleSyntax,
    function: FunctionNode,
    fixtures: dict[str, FunctionNode],
    seen: set[str] | None = None,
) -> int:
    """Mocks and non-environment patches in ``function`` and the fixtures it requests."""
    seen = seen if seen is not None else set()
    total = 0
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        patch = patch_call(syntax, node)
        if patch is not None:
            total += 0 if patch.environment else 1
        elif is_mock_factory(node):
            total += 1
    for arg in (
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    ):
        fixture = fixtures.get(arg.arg)
        if fixture is None or arg.arg in seen or fixture is function:
            continue
        seen.add(arg.arg)
        total += count_mocks(syntax, fixture, fixtures, seen)
    return total


class TooManyMocks(BaseRule):
    code = "SMT405"
    name = "too-many-mocks"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.FILES, Index.SYNTAX})
    doc = RuleDoc(
        summary="A test uses more mocks or patches than mocks.max_per_test.",
        rationale=(
            "Every mock is an assumption about a collaborator. Many of them mean the "
            "test mostly verifies its own setup, or the unit has too many dependencies."
        ),
        bad=(
            '@patch("app.a")\n@patch("app.b")\n@patch("app.c")\n@patch("app.d")\n'
            "def test_checkout(a, b, c, d): ..."
        ),
        good="def test_checkout(shop: FakeShop) -> None: ...",
        fix="Replace mocks with fakes, or test a smaller unit.",
        config=("tests.mocks.max_per_test",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        limit = ctx.config.tests.mocks.max_per_test
        for syntax in ctx.syntax.tests():
            functions = list(test_functions(syntax))
            if not functions:
                continue
            fixtures = visible_fixtures(ctx, syntax)
            for test in functions:
                count = count_mocks(syntax, test.node, fixtures)
                if count <= limit:
                    continue
                yield self.violation(
                    f"{test.qualname} uses {count} mocks or patches (max {limit})",
                    path=syntax.path,
                    line=test.node.lineno,
                    column=test.node.col_offset + 1,
                    expected={"max_per_test": limit},
                    hint="Replace mocks with fakes, or split the behavior under test.",
                )


class InteractionAssertion(BaseRule):
    code = "SMT406"
    name = "interaction-assertion"
    category = Category.TESTS
    default_severity = Severity.WARNING
    requires = frozenset({Index.FILES, Index.SYNTAX})
    doc = RuleDoc(
        summary="A test asserts how a first-party collaborator was called.",
        rationale=(
            "call_count and assert_called_* pin the implementation: the test fails when "
            "the code reaches the same result differently. Assert on results, state or "
            "emitted events. Boundary mocks (HTTP clients, the clock) are exempt."
        ),
        bad="sessions.save.assert_called_once_with(session)",
        good="assert sessions.saved == [session]",
        fix="Assert on the returned value or on the state of a fake.",
        config=("tests.interaction_assertions",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for syntax in ctx.syntax.tests():
            for test in test_functions(syntax):
                exempt = _boundary_mocks(ctx, syntax, test.node)
                yield from self._check_function(ctx, syntax, test.node, exempt)

    def _check_function(
        self,
        ctx: AnalysisContext,
        syntax: ModuleSyntax,
        function: FunctionNode,
        exempt: set[str],
    ) -> Iterator[Violation]:
        reported: set[int] = set()
        for node in ast.walk(function):
            attribute = _interaction(node)
            if attribute is None:
                continue
            receiver = dotted(attribute.value)
            if not receiver or receiver[0] in exempt or attribute.lineno in reported:
                continue
            reported.add(attribute.lineno)
            shown = ".".join((*receiver, attribute.attr))
            yield node_violation(
                self,
                ctx,
                syntax,
                attribute,
                f"asserts {shown}; assert on results, state or events instead",
                hint="Assert on the returned value or on the state of a fake.",
            )


def _interaction(node: ast.AST) -> ast.Attribute | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func if node.func.attr in _INTERACTION_METHODS else None
    if isinstance(node, ast.Assert):
        for child in ast.walk(node.test):
            if (
                isinstance(child, ast.Attribute)
                and child.attr in _INTERACTION_ATTRIBUTES
            ):
                return child
    return None


def _boundary_mocks(
    ctx: AnalysisContext, syntax: ModuleSyntax, function: FunctionNode
) -> set[str]:
    """Names bound to patches of external, stdlib or environment targets."""
    names: set[str] = set()
    for node in ast.walk(function):
        match node:
            case ast.withitem(context_expr=expr, optional_vars=ast.Name(id=name)):
                if _is_boundary(ctx, syntax, expr):
                    names.add(name)
            case ast.Assign(targets=[ast.Name(id=name)], value=expr):
                if _is_boundary(ctx, syntax, expr):
                    names.add(name)
    parameters = [
        arg.arg
        for arg in (*function.args.posonlyargs, *function.args.args)
        if arg.arg not in ("self", "cls")
    ]
    decorators = [
        decorator
        for decorator in reversed(function.decorator_list)
        if (patch := patch_call(syntax, decorator)) is not None
        and len(patch.node.args) < 2  # noqa: PLR2004 - `new` given: no mock argument
        and not any(k.arg == "new" for k in patch.node.keywords)
    ]
    for parameter, decorator in zip(parameters, decorators, strict=False):
        if _is_boundary(ctx, syntax, decorator):
            names.add(parameter)
    return names


def _is_boundary(ctx: AnalysisContext, syntax: ModuleSyntax, expr: ast.expr) -> bool:
    if (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr == "start"
    ):
        expr = expr.func.value
    patch = patch_call(syntax, expr)
    return patch is not None and bool(
        set(categories(ctx, patch)) & _BOUNDARY_CATEGORIES
    )
