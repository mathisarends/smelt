"""Scoped syntactic test evidence; no imports or execution of analyzed tests."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from smelt.analysis.exports import namespace_of, resolve_dotted
from smelt.analysis.parsing import resolve_relative
from smelt.analysis.test_deadlines import async_deadline
from smelt.config.patterns import path_matches

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

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
_MOCK_TYPES = frozenset(
    root + name for root in _MOCK_ROOTS for name in _MOCK_OPERATIONS - {"patch", "spy"}
)
_MOCK_ASSERTIONS = frozenset(
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
_MONKEYPATCH = "pytest.MonkeyPatch"
_MAX_RANGE_ARGS = 3
_CONSTRUCTORS = frozenset(
    {
        _MONKEYPATCH,
        "asyncio.Event",
        "asyncio.locks.Event",
        "asyncio.Task",
        "asyncio.Future",
        "threading.Event",
        "subprocess.Popen",
    }
)
_FACTORY_TYPES = {
    "asyncio.get_running_loop": "asyncio.AbstractEventLoop",
    "asyncio.get_event_loop": "asyncio.AbstractEventLoop",
    "asyncio.create_task": "asyncio.Task",
    "asyncio.ensure_future": "asyncio.Future",
}
_ASYNC_FACTORY_TYPES = {
    "asyncio.create_subprocess_exec": "asyncio.subprocess.Process",
    "asyncio.create_subprocess_shell": "asyncio.subprocess.Process",
}


@dataclass(frozen=True)
class _Binding:
    qualified: str
    kind: str = "symbol"
    provenance: str = "import"
    items: tuple[_Binding | None, ...] | None = None
    mock_spec: str | None = None
    async_callable: str | None = None
    mock_origin: str | None = field(default=None, compare=False)


def _annotation(node: ast.AST, env: Mapping[str, _Binding]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _annotation(ast.parse(node.value, mode="eval").body, env)
        except SyntaxError:
            return None
    if isinstance(node, ast.Name):
        binding = env.get(node.id)
        return binding.qualified if binding else None
    if isinstance(node, ast.Attribute):
        parent = _annotation(node.value, env)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _instance(qualified: str, provenance: str) -> _Binding:
    return _Binding(
        qualified,
        "mock" if qualified in _MOCK_TYPES else "instance",
        provenance,
        async_callable=qualified
        if qualified
        in {root + name for root in _MOCK_ROOTS for name in ("AsyncMock", "async_stub")}
        else None,
    )


def _fixture_binding(node: ast.AST, env: Mapping[str, _Binding]) -> _Binding | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _fixture_binding(ast.parse(node.value, mode="eval").body, env)
        except SyntaxError:
            return None
    if isinstance(node, ast.Subscript):
        origin = _annotation(node.value, env)
        arguments = (
            node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        )
        if origin in {
            "typing.Iterator",
            "typing.Generator",
            "typing.AsyncIterator",
            "typing.AsyncGenerator",
            "collections.abc.Iterator",
            "collections.abc.Generator",
            "collections.abc.AsyncIterator",
            "collections.abc.AsyncGenerator",
        }:
            return _fixture_binding(arguments[0], env) if arguments else None
        if origin in {"builtins.tuple", "typing.Tuple"}:
            return _Binding(
                "",
                "nonempty",
                "fixture-return",
                tuple(_fixture_binding(arg, env) for arg in arguments),
            )
    qualified = _annotation(node, env)
    return _instance(qualified, "fixture-return") if qualified else None


def _inferred_fixture(binding: _Binding) -> _Binding:
    return replace(
        binding,
        provenance="fixture-inferred",
        items=tuple(_inferred_fixture(item) if item else None for item in binding.items)
        if binding.items is not None
        else None,
    )


def _class_async_methods(tree: ast.Module, module: str) -> dict[str, frozenset[str]]:
    decorators = {"staticmethod", "classmethod"}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "abc":
            decorators.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "abstractmethod"
            )
        elif isinstance(node, ast.Import):
            decorators.update(
                f"{alias.asname or alias.name}.abstractmethod"
                for alias in node.names
                if alias.name == "abc"
            )
    return {
        f"{module}.{node.name}": frozenset(
            method.name
            for method in node.body
            if isinstance(method, ast.AsyncFunctionDef)
            and all(
                ast.unparse(decorator) in decorators
                for decorator in method.decorator_list
            )
        )
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }


def nonempty_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return bool(node.elts) and not any(
            isinstance(item, ast.Starred) for item in node.elts
        )
    if isinstance(node, ast.Dict):
        return bool(node.keys) and None not in node.keys
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str | bytes)
        and bool(node.value)
    )


def _nonempty_range(node: ast.Call) -> bool:
    if node.keywords or not node.args or len(node.args) > _MAX_RANGE_ARGS:
        return False
    values: list[int] = []
    for arg in node.args:
        sign = 1
        value = arg
        if isinstance(value, ast.UnaryOp) and isinstance(value.op, ast.USub):
            sign, value = -1, value.operand
        if (
            not isinstance(value, ast.Constant)
            or not isinstance(value.value, int)
            or isinstance(value.value, bool)
        ):
            return False
        values.append(sign * value.value)
    try:
        sequence = range(*values)
    except ValueError:
        return False
    return (
        sequence.start < sequence.stop
        if sequence.step > 0
        else sequence.start > sequence.stop
    )


@dataclass(frozen=True)
class TestCall:
    node: ast.Call
    qualified: str
    provenance: str = "import"
    mock_receiver: bool = False

    @property
    def patch(self) -> bool:
        return self.qualified in {
            f"{_MONKEYPATCH}.setattr",
            f"{_MONKEYPATCH}.delattr",
        } or self.qualified in {
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
    def family(self) -> str:
        if self.qualified.startswith(f"{_MONKEYPATCH}."):
            return "pytest.MonkeyPatch"
        if self.qualified.startswith("pytest_mock."):
            return "pytest-mock"
        return "unittest.mock"

    @property
    def interaction_assertion(self) -> bool:
        return (
            self.mock_receiver and self.qualified.rsplit(".", 1)[-1] in _MOCK_ASSERTIONS
        )

    @property
    def string_target(self) -> bool:
        if not self.patch or self.qualified.endswith(".object"):
            return False
        if self.qualified.startswith(f"{_MONKEYPATCH}."):
            # setattr("pkg.name", replacement) vs setattr(obj, "name", replacement).
            positional = 2 if self.qualified.endswith(".setattr") else 1
            if len(self.node.args) > positional or any(
                kw.arg == "name" for kw in self.node.keywords
            ):
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
class MockObservation:
    node: ast.Call | ast.Attribute
    origin: str
    receiver: str
    kind: str
    async_callable: str
    provenance: str


@dataclass
class TestFunction:
    node: Function
    name: str
    calls: list[TestCall] = field(default_factory=list)
    assertions: list[ast.Assert] = field(default_factory=list)
    clock_replacements: list[ast.Attribute | ast.Call] = field(default_factory=list)
    awaited_tasks: list[tuple[ast.Await, str, str]] = field(default_factory=list)
    nonempty_iterables: set[ast.expr] = field(default_factory=set)
    mock_observations: list[MockObservation] = field(default_factory=list)

    def has_assertion(self, helpers: list[str]) -> bool:
        return bool(self.outcome_evidence(helpers))

    def outcome_evidence(self, helpers: list[str]) -> list[ast.Assert | ast.Call]:
        evidence: list[ast.Assert | ast.Call] = [
            node
            for node in self.assertions
            if not (isinstance(node.test, ast.Constant) and node.test.value is True)
        ]
        parents = {
            child: node
            for node in ast.walk(self.node)
            for child in ast.iter_child_nodes(node)
        }
        evidence.extend(
            call.node
            for call in self.calls
            if (
                call.qualified in helpers
                or (
                    call.qualified
                    in {"pytest.raises", "pytest.warns", "pytest.deprecated_call"}
                    and (
                        isinstance(parents.get(call.node), ast.withitem)
                        or len(call.node.args)
                        > (0 if call.qualified == "pytest.deprecated_call" else 1)
                    )
                )
                or call.qualified.startswith("unittest.TestCase.assert")
                or call.interaction_assertion
            )
        )
        calls = {call.node: call.qualified for call in self.calls}
        evidence.extend(
            call.node
            for call in self.calls
            if (
                call.qualified in {"asyncio.Event.wait", "asyncio.locks.Event.wait"}
                and (deadline := async_deadline(call.node, calls, parents)).awaited
                and deadline.raises_on_timeout
            )
        )
        return evidence


def _patch_result(node: ast.Call, qualified: str) -> _Binding | None:
    new_position = 2 if qualified.endswith(".object") else 1
    if len(node.args) > new_position or any(
        kw.arg in {"new", "new_callable"} or kw.arg is None for kw in node.keywords
    ):
        return None
    return _instance("unittest.mock.MagicMock", "patch-result")


@dataclass
class TestModule:
    path: str
    imports: list[tuple[ast.Import | ast.ImportFrom, str]] = field(default_factory=list)
    attributes: list[tuple[ast.Attribute, str]] = field(default_factory=list)
    calls: list[TestCall] = field(default_factory=list)
    tests: list[TestFunction] = field(default_factory=list)
    instance_attributes: list[tuple[ast.Attribute, str, str]] = field(
        default_factory=list
    )


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
    def __init__(
        self,
        module: TestModule,
        constructors: frozenset[str],
        fixtures: Mapping[str, _Binding],
        async_specs: Mapping[str, frozenset[str]],
    ) -> None:
        self.result = module
        self.env: dict[str, _Binding] = {
            name: _Binding(f"builtins.{name}") for name in ("all", "range", "tuple")
        }
        self.module_env = self.env
        self.constructors = constructors
        self.fixtures = fixtures
        self.async_specs = async_specs
        self.defined_fixtures: dict[str, _Binding] = {}
        self.fixture_outputs: list[_Binding | None] | None = None
        self.fixture_yielded = False
        self.test: TestFunction | None = None
        self.function_depth = 0
        self.class_name: str | None = None
        self.unittest = False

    def qualified(self, node: ast.AST) -> str | None:
        binding = self.binding(node)
        return binding.qualified if binding else None

    def binding(self, node: ast.AST) -> _Binding | None:
        if isinstance(node, ast.Name):
            return self.env.get(node.id)
        if isinstance(node, ast.Call | ast.Await):
            return self.value_binding(node)
        if isinstance(node, ast.Attribute):
            parent = self.binding(node.value)
            if parent and parent.kind != "member":
                return _Binding(
                    f"{parent.qualified}.{node.attr}",
                    "mock"
                    if parent.kind == "mock"
                    else "member"
                    if parent.kind == "instance"
                    else "symbol",
                    parent.provenance,
                    async_callable=f"{parent.mock_spec}.{node.attr}"
                    if parent.mock_spec
                    and node.attr in self.async_specs.get(parent.mock_spec, ())
                    else None,
                    mock_origin=f"{parent.mock_origin}.{node.attr}"
                    if parent.mock_origin
                    else None,
                )
        return None

    def value_binding(self, node: ast.AST, *, context: bool = False) -> _Binding | None:
        if isinstance(node, ast.Await):
            qualified = (
                self.qualified(node.value.func)
                if isinstance(node.value, ast.Call)
                else None
            )
            return (
                _instance(_ASYNC_FACTORY_TYPES[qualified], "awaited-factory")
                if qualified in _ASYNC_FACTORY_TYPES
                else None
            )
        if (
            isinstance(node, ast.Tuple)
            and node.elts
            and not any(isinstance(item, ast.Starred) for item in node.elts)
        ):
            return _Binding(
                "",
                "nonempty",
                "literal",
                tuple(self.value_binding(item) for item in node.elts),
            )
        if isinstance(node, ast.Tuple | ast.Constant) and nonempty_literal(node):
            return _Binding("", "nonempty", "literal")
        if isinstance(node, ast.Call):
            return self.call_result(node, context=context)
        return self.binding(node)

    def call_result(self, node: ast.Call, *, context: bool) -> _Binding | None:
        qualified = self.qualified(node.func)
        if qualified == "builtins.range":
            return (
                _Binding("", "nonempty", "literal-range")
                if _nonempty_range(node)
                else None
            )
        if qualified in self.constructors | _MOCK_TYPES | _CONSTRUCTORS:
            binding = _instance(qualified, "constructor")
            if binding.kind == "mock":
                spec = next(
                    (
                        kw.value
                        for kw in node.keywords
                        if kw.arg in {"spec", "spec_set"}
                        and not isinstance(kw.value, ast.Constant)
                    ),
                    node.args[0] if node.args else None,
                )
                binding = replace(
                    binding,
                    mock_spec=self.qualified(spec) if spec else None,
                    mock_origin=f"{self.result.path}:{node.lineno}:{node.col_offset}",
                )
            return binding
        if qualified in _FACTORY_TYPES:
            return _instance(_FACTORY_TYPES[qualified], "known-factory")
        if qualified == f"{_MONKEYPATCH}.context":
            return _instance(_MONKEYPATCH, "context")
        if (
            qualified
            and TestCall(node, qualified).patch
            and not qualified.endswith((".dict", ".multiple"))
            and not qualified.startswith(f"{_MONKEYPATCH}.")
            and (context or qualified.startswith("pytest_mock."))
        ):
            return _patch_result(node, qualified)
        return None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.env[alias.asname or alias.name.split(".")[0]] = _Binding(
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
            self.env[alias.asname or alias.name] = _Binding(qualified)
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
        self.env = outer
        self.class_name = class_name
        self.unittest = unittest
        self.env.pop(node.name, None)
        qualified = (
            f"{self.result.path.removesuffix('.py').replace('/', '.')}.{node.name}"
        )
        if qualified in self.async_specs:
            self.env[node.name] = _Binding(qualified)

    def visit_FunctionDef(self, node: Function) -> None:
        outer, test = self.env, self.test
        outer_outputs = self.fixture_outputs
        outer_yielded = self.fixture_yielded
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
            class_fixture = f"{self.class_name}.{arg.arg}"
            fixture_key = class_fixture if class_fixture in self.fixtures else arg.arg
            annotation = (
                _annotation(arg.annotation, self.env) if arg.annotation else None
            )
            if (
                self.function_depth == 0
                and (collected or fixture)
                and fixture_key in self.fixtures
            ):
                self.env[arg.arg] = self.fixtures[fixture_key]
            if annotation:
                existing = self.env.get(arg.arg)
                self.env[arg.arg] = (
                    existing
                    if existing and existing.qualified == annotation
                    else _instance(annotation, "annotation")
                )
            binding = self.env.get(arg.arg)
            if binding and binding.kind == "mock" and binding.mock_origin is None:
                self.env[arg.arg] = replace(
                    binding,
                    mock_origin=f"{self.result.path}:{node.lineno}:{arg.arg}",
                )
            if self.function_depth == 0 and (
                (
                    arg.arg in _MOCKER_FIXTURES
                    and (collected or fixture)
                    and fixture_key not in self.fixtures
                    and annotation is None
                )
                or (
                    arg.annotation is not None
                    and self.qualified(arg.annotation) == "pytest_mock.MockerFixture"
                )
            ):
                self.env[arg.arg] = _Binding(
                    "pytest_mock.MockerFixture", provenance="fixture"
                )
            if (
                self.function_depth == 0
                and arg.arg == "monkeypatch"
                and (collected or fixture)
                and fixture_key not in self.fixtures
                and annotation is None
            ):
                self.env[arg.arg] = _instance(_MONKEYPATCH, "fixture")
        if self.unittest and node.args.args:
            self.env[node.args.args[0].arg] = _instance(
                "unittest.TestCase", "unittest-self"
            )
        self.fixture_outputs = [] if fixture else None
        self.fixture_yielded = False
        self.function_depth += 1
        for statement in node.body:
            self.visit(statement)
        self.function_depth -= 1
        if fixture:
            self.record_fixture(node, outer)
        self.env = outer
        self.test = test
        self.fixture_outputs = outer_outputs
        self.fixture_yielded = outer_yielded
        self.env.pop(node.name, None)

    def record_fixture(self, node: Function, env: Mapping[str, _Binding]) -> None:
        if self.function_depth != 0:
            return
        binding = _fixture_binding(node.returns, env) if node.returns else None
        outputs = self.fixture_outputs
        if (
            outputs
            and outputs[0] is not None
            and all(output == outputs[0] for output in outputs)
            and (self.fixture_yielded or isinstance(node.body[-1], ast.Return))
        ):
            inferred = _inferred_fixture(outputs[0])
            if not node.returns:
                binding = inferred
            elif (
                binding
                and binding.kind == "mock"
                and binding.qualified == inferred.qualified
            ):
                binding = replace(inferred, provenance="fixture-return")
        fixture_name = node.name
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call):
                for keyword in decorator.keywords:
                    if (
                        keyword.arg == "name"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ):
                        fixture_name = keyword.value.value
        fixture_key = (
            f"{self.class_name}.{fixture_name}" if self.class_name else fixture_name
        )
        self.defined_fixtures[fixture_key] = binding or _Binding(
            "", provenance="unknown-fixture"
        )

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_Call(self, node: ast.Call) -> None:
        binding = self.binding(node.func)
        if binding:
            call = TestCall(
                node, binding.qualified, binding.provenance, binding.kind == "mock"
            )
            self.result.calls.append(call)
            if self.test:
                self.test.calls.append(call)
                result = self.value_binding(node)
                if result and result.kind == "nonempty":
                    self.test.nonempty_iterables.add(node)
                if (
                    binding.qualified == f"{_MONKEYPATCH}.setattr"
                    and len(node.args) > 1
                    and self.qualified(node.args[0]) == "asyncio.AbstractEventLoop"
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value == "time"
                ):
                    self.test.clock_replacements.append(node)
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name in _MOCK_ASSERTIONS and name != "assert_not_called":
                self.record_mock_observation(
                    node,
                    node.func.value,
                    "await" if "await" in name else "call",
                )
        self.generic_visit(node)

    def record_mock_observation(
        self, node: ast.Call | ast.Attribute, receiver: ast.expr, kind: str
    ) -> None:
        binding = self.binding(receiver)
        if self.test and binding and binding.async_callable and binding.mock_origin:
            self.test.mock_observations.append(
                MockObservation(
                    node,
                    binding.mock_origin,
                    ast.unparse(receiver),
                    kind,
                    binding.async_callable,
                    binding.provenance,
                )
            )

    def visit_Name(self, node: ast.Name) -> None:
        binding = self.env.get(node.id)
        if (
            self.test
            and isinstance(node.ctx, ast.Load)
            and binding
            and binding.kind == "nonempty"
        ):
            self.test.nonempty_iterables.add(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        qualified = self.qualified(node)
        receiver = self.binding(node.value)
        if (
            qualified == "asyncio.AbstractEventLoop.time"
            and isinstance(node.ctx, ast.Store)
            and self.test
        ):
            self.test.clock_replacements.append(node)
        if qualified and receiver and receiver.kind == "instance":
            if isinstance(node.ctx, ast.Load):
                self.result.instance_attributes.append(
                    (node, qualified, receiver.provenance)
                )
        elif qualified:
            self.result.attributes.append((node, qualified))
        if isinstance(node.ctx, ast.Load):
            if node.attr in {"called", "call_count", "call_args", "call_args_list"}:
                self.record_mock_observation(node, node.value, "call")
            elif node.attr in {"await_count", "await_args", "await_args_list"}:
                self.record_mock_observation(node, node.value, "await")
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        if self.test:
            self.test.assertions.append(node)
        self.generic_visit(node)

    def visit_Await(self, node: ast.Await) -> None:
        binding = self.binding(node.value)
        if (
            self.test
            and binding
            and binding.kind == "instance"
            and binding.qualified in {"asyncio.Task", "asyncio.Future"}
        ):
            self.test.awaited_tasks.append(
                (node, binding.qualified, binding.provenance)
            )
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        self.generic_visit(node)
        if self.fixture_outputs is not None:
            self.fixture_outputs.append(
                self.value_binding(node.value) if node.value else None
            )

    def visit_Yield(self, node: ast.Yield) -> None:
        self.generic_visit(node)
        if self.fixture_outputs is not None:
            self.fixture_yielded = True
            self.fixture_outputs.append(
                self.value_binding(node.value) if node.value else None
            )

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self.generic_visit(node)
        if self.fixture_outputs is not None:
            self.fixture_outputs.append(None)

    def bind_target(self, target: ast.AST, binding: _Binding | None) -> None:
        for name in _names(target):
            self.env.pop(name, None)
        if isinstance(target, ast.Name) and binding:
            self.env[target.id] = (
                replace(
                    binding,
                    mock_origin=f"{self.result.path}:{target.lineno}:{target.id}",
                )
                if binding.kind == "mock" and binding.mock_origin is None
                else binding
            )
        elif (
            isinstance(target, ast.Tuple | ast.List)
            and binding
            and binding.items is not None
            and len(target.elts) == len(binding.items)
        ):
            for item, value in zip(target.elts, binding.items, strict=True):
                self.bind_target(item, value)

    def forget_mock_spec(self, target: ast.AST) -> None:
        if not isinstance(target, ast.Attribute):
            return
        receiver = self.binding(target.value)
        if receiver and receiver.mock_spec and receiver.mock_origin:
            for name, binding in list(self.env.items()):
                if binding.mock_origin == receiver.mock_origin:
                    self.env[name] = replace(binding, mock_spec=None)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        binding = self.value_binding(node.value)
        for target in node.targets:
            self.visit(target)
            self.forget_mock_spec(target)
            self.bind_target(target, binding)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.target)
        if node.value:
            self.visit(node.value)
            self.forget_mock_spec(node.target)
        for name in _names(node.target):
            self.env.pop(name, None)
        annotation = _annotation(node.annotation, self.env)
        if isinstance(node.target, ast.Name) and annotation:
            value = self.value_binding(node.value) if node.value else None
            binding = (
                value
                if value and value.qualified == annotation
                else _instance(annotation, "annotation")
            )
            self.bind_target(node.target, binding)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        for name in _names(node.target):
            self.env.pop(name, None)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self.forget_mock_spec(target)
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
            binding = self.value_binding(item.context_expr, context=True)
            if item.optional_vars:
                for name in _names(item.optional_vars):
                    self.env.pop(name, None)
                if isinstance(item.optional_vars, ast.Name) and binding:
                    self.env[item.optional_vars.id] = binding
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
        self.env = dict(outer)
        self.test = None
        for arg in [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
            *[a for a in (node.args.vararg, node.args.kwarg) if a],
        ]:
            self.env.pop(arg.arg, None)
        self.visit(node.body)
        self.env = outer
        self.test = test

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
        # Only the outer iterable is evaluated in this scope at construction.
        binding = self.value_binding(node.generators[0].iter)
        if test and binding and binding.kind == "nonempty":
            test.nonempty_iterables.add(node.generators[0].iter)
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


def _declared_fixtures(
    tree: ast.Module,
    path: str,
    constructors: frozenset[str],
    inherited: Mapping[str, _Binding],
    async_specs: Mapping[str, frozenset[str]],
) -> dict[str, _Binding]:
    """Annotations or consistent known returns/yields, never fixture execution."""
    visitor = _Visitor(TestModule(path), constructors, inherited, async_specs)
    visitor.visit(tree)
    return visitor.defined_fixtures


def analyze_test_syntax(
    tree: ast.Module,
    path: str,
    *,
    constructors: frozenset[str] = frozenset(),
    fixtures: Mapping[str, _Binding] | None = None,
    async_specs: Mapping[str, frozenset[str]] | None = None,
) -> TestModule:
    module = TestModule(path)
    specs = {
        **(async_specs or {}),
        **_class_async_methods(tree, path.removesuffix(".py").replace("/", ".")),
    }
    _Visitor(
        module,
        constructors,
        {
            **(fixtures or {}),
            **_declared_fixtures(tree, path, constructors, fixtures or {}, specs),
        },
        specs,
    ).visit(tree)
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
            or config.require_non_vacuous_assertions
            or config.review_test_deadlines
            or config.review_case_loops
            or config.review_async_mock_assertions
            or config.review_test_only_public_functions
        )
        if not enabled:
            return
        source_specs = (
            {
                qualified: methods
                for source in files.sources.values()
                for qualified, methods in _class_async_methods(
                    asts.parse(source.path), source.module
                ).items()
            }
            if config.forbid_private_imports or config.review_async_mock_assertions
            else {}
        )
        constructors = (
            frozenset(source_specs) if config.forbid_private_imports else frozenset()
        )
        async_specs = dict(source_specs) if config.review_async_mock_assertions else {}
        if source_specs:
            namespaces = {
                source.module: namespace_of(
                    asts.parse(source.path), source.module, is_package=source.is_package
                )
                for source in files.sources.values()
            }
            aliases = {
                f"{module}.{name}": f"{resolved[0]}.{resolved[1]}"
                for module, namespace in namespaces.items()
                for name in namespace.imported
                if (resolved := resolve_dotted(f"{module}.{name}", namespaces.get))
                is not None
                and f"{resolved[0]}.{resolved[1]}" in source_specs
            }
            if config.forbid_private_imports:
                constructors |= frozenset(aliases)
            if config.review_async_mock_assertions:
                async_specs.update(
                    (alias, source_specs[target]) for alias, target in aliases.items()
                )
        fixture_scopes: dict[str, dict[str, _Binding]] = {}
        for path in sorted(files.tests, key=lambda path: (path.count("/"), path)):
            if not path.endswith("conftest.py") or any(
                path_matches(pattern, path) for pattern in config.quality_exclude
            ):
                continue
            inherited = {
                name: binding
                for scope, bindings in fixture_scopes.items()
                if path.startswith(scope)
                for name, binding in bindings.items()
            }
            async_specs.update(
                _class_async_methods(
                    asts.parse(path), path.removesuffix(".py").replace("/", ".")
                )
            )
            fixture_scopes[path.removesuffix("conftest.py")] = _declared_fixtures(
                asts.parse(path),
                path,
                constructors,
                inherited,
                async_specs,
            )
        for path in sorted(files.tests):
            if any(path_matches(pattern, path) for pattern in config.quality_exclude):
                continue
            fixtures = {
                name: binding
                for scope in sorted(fixture_scopes, key=len)
                if path.startswith(scope)
                for name, binding in fixture_scopes[scope].items()
            }
            self.modules[path] = analyze_test_syntax(
                asts.parse(path),
                path,
                constructors=constructors,
                fixtures=fixtures,
                async_specs=async_specs,
            )
