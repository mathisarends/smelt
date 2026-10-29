"""Scoped syntactic test evidence; no imports or execution of analyzed tests."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smelt.analysis.parsing import resolve_relative
from smelt.config.patterns import path_matches

if TYPE_CHECKING:
    from collections.abc import Sequence

    from smelt.analysis.files import FileIndex
    from smelt.analysis.parsing import AstCache

type Function = ast.FunctionDef | ast.AsyncFunctionDef

_MOCKER_FIXTURES = frozenset(
    {"mocker", "class_mocker", "module_mocker", "package_mocker", "session_mocker"}
)
_MOCK_ROOTS = ("unittest.mock.", "pytest_mock.MockerFixture.")
_MOCK_OPERATIONS = frozenset(
    {
        "Mock",
        "MagicMock",
        "AsyncMock",
        "NonCallableMock",
        "NonCallableMagicMock",
        "PropertyMock",
        "mock_open",
        "create_autospec",
        "patch",
        "spy",
        "stub",
        "async_stub",
    }
)


@dataclass(frozen=True)
class TestCall:
    node: ast.Call
    qualified: str

    @property
    def patch(self) -> bool:
        return self.qualified in {
            root + suffix
            for root in _MOCK_ROOTS
            for suffix in ("patch", "patch.object", "patch.dict", "patch.multiple")
        }

    @property
    def mock(self) -> bool:
        return self.patch or any(
            self.qualified == root + name
            for root in _MOCK_ROOTS
            for name in _MOCK_OPERATIONS
        )

    @property
    def string_target(self) -> bool:
        if not self.patch or self.qualified.endswith(".object"):
            return False
        target = (
            self.node.args[0]
            if self.node.args
            else next(
                (
                    kw.value
                    for kw in self.node.keywords
                    if kw.arg in {"target", "in_dict"}
                ),
                None,
            )
        )
        return isinstance(target, ast.Constant) and isinstance(target.value, str)


@dataclass
class TestFunction:
    node: Function
    name: str
    calls: list[TestCall] = field(default_factory=list)
    assertions: list[ast.Assert] = field(default_factory=list)

    def has_assertion(self, helpers: list[str]) -> bool:
        if any(
            not (isinstance(node.test, ast.Constant) and node.test.value is True)
            for node in self.assertions
        ):
            return True
        return any(
            call.qualified
            in {"pytest.raises", "pytest.warns", "pytest.deprecated_call", *helpers}
            or call.qualified.startswith("unittest.TestCase.assert")
            for call in self.calls
        )


@dataclass
class TestModule:
    path: str
    imports: list[tuple[ast.Import | ast.ImportFrom, str]] = field(default_factory=list)
    attributes: list[tuple[ast.Attribute, str]] = field(default_factory=list)
    calls: list[TestCall] = field(default_factory=list)
    tests: list[TestFunction] = field(default_factory=list)


def _names(node: ast.AST) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store | ast.Del)
    }


def _locals(nodes: Sequence[ast.AST]) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
            continue
        if isinstance(node, ast.Import | ast.ImportFrom):
            names.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        elif not isinstance(
            node,
            ast.Lambda | ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        ):
            names.update(
                child.id
                for child in ast.iter_child_nodes(node)
                if isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Store | ast.Del)
            )
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.stmt):
                    names.update(_locals([child]))
                elif not isinstance(child, ast.expr_context):
                    names.update(_expression_bindings(child))
    return names


def _expression_bindings(node: ast.AST) -> set[str]:
    if isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
        return {node.name}
    if isinstance(
        node, ast.Lambda | ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ):
        return set()
    return (
        {node.id}
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del)
        else set()
    ) | set().union(
        *(_expression_bindings(child) for child in ast.iter_child_nodes(node))
    )


class _Visitor(ast.NodeVisitor):
    def __init__(self, module: TestModule) -> None:
        self.result = module
        self.env: dict[str, str] = {}
        self.module_env = self.env
        self.test: TestFunction | None = None
        self.function_depth = 0
        self.class_name: str | None = None
        self.unittest = False

    def qualified(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.env.get(node.id)
        if isinstance(node, ast.Attribute):
            parent = self.qualified(node.value)
            return f"{parent}.{node.attr}" if parent else None
        return None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.env[alias.asname or alias.name.split(".")[0]] = (
                alias.name if alias.asname else alias.name.split(".")[0]
            )
            self.result.imports.append((node, alias.name))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = self.result.path.removesuffix(".py").replace("/", ".")
        base = resolve_relative(
            module, is_package=False, level=node.level, target=node.module
        )
        for alias in node.names:
            if alias.name == "*":
                self.env.clear()
                continue
            qualified = f"{base}.{alias.name}" if base else alias.name
            self.env[alias.asname or alias.name] = qualified
            self.result.imports.append((node, qualified))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        outer, class_name, unittest = self.env, self.class_name, self.unittest
        for expression in [
            *node.decorator_list,
            *node.bases,
            *(kw.value for kw in node.keywords),
        ]:
            self.visit(expression)
        self.unittest = any(
            self.qualified(base)
            in {"unittest.TestCase", "unittest.IsolatedAsyncioTestCase"}
            for base in node.bases
        )
        self.class_name = node.name
        self.env = dict(outer)
        for statement in node.body:
            self.visit(statement)
        self.env, self.class_name, self.unittest = outer, class_name, unittest
        self.env.pop(node.name, None)

    def visit_FunctionDef(self, node: Function) -> None:
        outer, test = self.env, self.test
        fixture = any(
            self.qualified(
                decorator.func if isinstance(decorator, ast.Call) else decorator
            )
            in {"pytest.fixture", "pytest_asyncio.fixture"}
            for decorator in node.decorator_list
        )
        collected = (
            self.function_depth == 0
            and node.name.startswith("test_")
            and (
                self.class_name is None
                or self.class_name.startswith("Test")
                or self.unittest
            )
        )
        self.test = (
            TestFunction(
                node, f"{self.class_name}.{node.name}" if self.class_name else node.name
            )
            if collected
            else None
        )
        if self.test:
            self.result.tests.append(self.test)
        for expression in [
            *node.decorator_list,
            *node.args.defaults,
            *(value for value in node.args.kw_defaults if value is not None),
        ]:
            self.visit(expression)
        self.env = dict(
            self.module_env if self.class_name and self.function_depth == 0 else outer
        )
        for name in _locals(node.body):
            self.env.pop(name, None)
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        arguments += [
            arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None
        ]
        for arg in arguments:
            self.env.pop(arg.arg, None)
            if self.function_depth == 0 and (
                (arg.arg in _MOCKER_FIXTURES and (collected or fixture))
                or (
                    arg.annotation is not None
                    and self.qualified(arg.annotation) == "pytest_mock.MockerFixture"
                )
            ):
                self.env[arg.arg] = "pytest_mock.MockerFixture"
        if self.unittest and node.args.args:
            self.env[node.args.args[0].arg] = "unittest.TestCase"
        self.function_depth += 1
        for statement in node.body:
            self.visit(statement)
        self.function_depth -= 1
        self.env, self.test = outer, test
        self.env.pop(node.name, None)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Call(self, node: ast.Call) -> None:
        qualified = self.qualified(node.func)
        if qualified:
            call = TestCall(node, qualified)
            self.result.calls.append(call)
            if self.test:
                self.test.calls.append(call)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        qualified = self.qualified(node)
        if qualified:
            self.result.attributes.append((node, qualified))
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        if self.test:
            self.test.assertions.append(node)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            for name in _names(target):
                self.env.pop(name, None)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:
            self.visit(node.value)
            for name in _names(node.target):
                self.env.pop(name, None)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        for name in _names(node.target):
            self.env.pop(name, None)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            for name in _names(target):
                self.env.pop(name, None)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._branches([node.body, node.orelse])

    def _branches(self, bodies: Sequence[Sequence[ast.AST]]) -> None:
        outer = self.env
        for body in bodies:
            self.env = dict(outer)
            for statement in body:
                self.visit(statement)
        self.env = outer
        for name in _locals([statement for body in bodies for statement in body]):
            self.env.pop(name, None)

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        for name in _names(node.target):
            self.env.pop(name, None)
        self._branches([node.body, node.orelse])

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit_For(node)

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._branches([node.body, node.orelse])

    def visit_With(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars:
                for name in _names(item.optional_vars):
                    self.env.pop(name, None)
        for statement in node.body:
            self.visit(statement)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.visit_With(node)

    def visit_Try(self, node: ast.Try | ast.TryStar) -> None:
        self._branches(
            [node.body, *([handler] for handler in node.handlers), node.orelse]
        )
        for statement in node.finalbody:
            self.visit(statement)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self.visit_Try(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type:
            self.visit(node.type)
        if node.name:
            self.env.pop(node.name, None)
        for statement in node.body:
            self.visit(statement)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        for name in _names(node.target):
            self.env.pop(name, None)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        outer, test = self.env, self.test
        for value in [
            *node.args.defaults,
            *(v for v in node.args.kw_defaults if v is not None),
        ]:
            self.visit(value)
        self.env, self.test = dict(outer), None
        for arg in [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            *[a for a in (node.args.vararg, node.args.kwarg) if a],
        ]:
            self.env.pop(arg.arg, None)
        self.visit(node.body)
        self.env, self.test = outer, test

    def visit_ListComp(
        self, node: ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp
    ) -> None:
        outer = self.env
        self.env = dict(outer)
        for generator in node.generators:
            self.visit(generator.iter)
            for name in _names(generator.target):
                self.env.pop(name, None)
            for condition in generator.ifs:
                self.visit(condition)
        for value in (
            [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        ):
            self.visit(value)
        self.env = outer

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self.visit_ListComp(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self.visit_ListComp(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        test = self.test
        self.test = None
        self.visit_ListComp(node)
        self.test = test

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        outer = self.env
        for case in node.cases:
            self.env = dict(outer)
            for name in _expression_bindings(case.pattern):
                self.env.pop(name, None)
            if case.guard:
                self.visit(case.guard)
            for statement in case.body:
                self.visit(statement)
        self.env = outer
        for name in _locals([node]):
            self.env.pop(name, None)


def analyze_test_syntax(tree: ast.Module, path: str) -> TestModule:
    module = TestModule(path)
    _Visitor(module).visit(tree)
    return module


class TestSyntaxIndex:
    def __init__(self, files: FileIndex, asts: AstCache) -> None:
        self.modules: dict[str, TestModule] = {}
        config = files.config.tests
        enabled = (
            config.forbid_private_imports
            or config.forbid_mocks_in
            or config.forbid_string_patch_targets
            or config.max_patches_per_test is not None
            or config.require_assertions
        )
        if not enabled:
            return
        for path in sorted(files.tests):
            if any(path_matches(pattern, path) for pattern in config.quality_exclude):
                continue
            self.modules[path] = analyze_test_syntax(asts.parse(path), path)
