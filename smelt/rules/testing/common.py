import ast
import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.imports import is_stdlib
from smelt.model import ModuleKind

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.context import AnalysisContext
    from smelt.analysis.syntax import ModuleSyntax

type FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef

MOCK_FACTORIES = frozenset(
    {
        "Mock",
        "MagicMock",
        "AsyncMock",
        "NonCallableMock",
        "NonCallableMagicMock",
        "PropertyMock",
        "create_autospec",
    }
)
_ENV_PATCHERS = frozenset({"setenv", "delenv"})


def dotted(node: ast.expr) -> tuple[str, ...]:
    """``mocker.patch.object`` -> ("mocker", "patch", "object"); () if not a plain chain."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return tuple(reversed(parts))
    return ()


def is_private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


@dataclass(frozen=True, slots=True)
class TestFunction:
    node: FunctionNode
    qualname: str


def test_functions(syntax: ModuleSyntax) -> Iterator[TestFunction]:
    for statement in syntax.tree.body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            if statement.name.startswith("test"):
                yield TestFunction(statement, statement.name)
        elif isinstance(statement, ast.ClassDef) and statement.name.startswith("Test"):
            for member in statement.body:
                if isinstance(
                    member, ast.FunctionDef | ast.AsyncFunctionDef
                ) and member.name.startswith("test"):
                    yield TestFunction(member, f"{statement.name}.{member.name}")


def fixtures(syntax: ModuleSyntax) -> dict[str, FunctionNode]:
    found: dict[str, FunctionNode] = {}
    for node in ast.walk(syntax.tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if dotted(target)[-1:] == ("fixture",):
                found[node.name] = node
    return found


def visible_fixtures(
    ctx: AnalysisContext, syntax: ModuleSyntax
) -> dict[str, FunctionNode]:
    """Fixtures of the test module and of every conftest.py above it."""
    found: dict[str, FunctionNode] = {}
    directory = posixpath.dirname(syntax.path)
    chain: list[str] = []
    while True:
        chain.append(
            posixpath.join(directory, "conftest.py") if directory else "conftest.py"
        )
        if not directory:
            break
        directory = posixpath.dirname(directory)
    for path in reversed(chain):
        if path in ctx.files.tests and path != syntax.path:
            conftest = ctx.syntax.for_path(path)
            if conftest is not None:
                found.update(fixtures(conftest))
    found.update(fixtures(syntax))
    return found


@dataclass(frozen=True, slots=True)
class Patch:
    node: ast.Call
    target: str | None  # qualified name, None if it cannot be resolved
    attribute: str | None  # patched attribute name, when known
    environment: bool = False


def patch_call(syntax: ModuleSyntax, node: ast.expr) -> Patch | None:
    """Recognise mock.patch / patch.object / mocker.patch / monkeypatch.* calls."""
    if not isinstance(node, ast.Call):
        return None
    match _patch_kind(syntax, dotted(node.func)):
        case "environment":
            return Patch(node, None, None, environment=True)
        case "dict":
            return _dict_patch(syntax, node)
        case "object":
            return _object_patch(syntax, node)
        case "target" if node.args:
            return _target_patch(syntax, node)
        case _:
            return None


def _patch_kind(syntax: ModuleSyntax, parts: tuple[str, ...]) -> str | None:
    last = parts[-1] if parts else None
    kind: str | None = None
    if last in _ENV_PATCHERS:
        kind = "environment"
    elif last in ("dict", "object") and "patch" in parts:
        kind = last
    elif (last == "patch" and _is_mock_patch(syntax, parts)) or (
        last in ("setattr", "delattr") and parts[0] == "monkeypatch"
    ):
        kind = "target"
    return kind


def _target_patch(syntax: ModuleSyntax, node: ast.Call) -> Patch:
    text = _string(node.args[0])
    if text is None:
        return _object_patch(syntax, node)
    return Patch(node, text, text.rsplit(".", 1)[-1])


def _dict_patch(syntax: ModuleSyntax, node: ast.Call) -> Patch:
    target = _string(node.args[0]) if node.args else None
    environment = target == "os.environ" or (
        bool(node.args) and syntax.resolve(node.args[0]) == "os.environ"
    )
    return Patch(node, target, None, environment=environment)


def _is_mock_patch(syntax: ModuleSyntax, parts: tuple[str, ...]) -> bool:
    if parts[0] == "mocker":
        return True
    root = syntax.bindings.get(parts[0], parts[0])
    return ".".join((root, *parts[1:])) in ("unittest.mock.patch", "mock.patch")


def _object_patch(syntax: ModuleSyntax, node: ast.Call) -> Patch:
    owner = node.args[0] if node.args else None
    attribute = _string(node.args[1]) if len(node.args) > 1 else None
    base = syntax.resolve(owner) if owner is not None else None
    target = f"{base}.{attribute}" if base and attribute else None
    return Patch(node, target, attribute)


def _string(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def patches_in(syntax: ModuleSyntax, node: ast.AST) -> Iterator[Patch]:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            found = patch_call(syntax, child)
            if found is not None:
                yield found


def is_mock_factory(node: ast.Call) -> bool:
    parts = dotted(node.func)
    return bool(parts) and parts[-1] in MOCK_FACTORIES


def mock_spec(node: ast.Call) -> ast.expr | None:
    """The class a mock is modelled on: ``create_autospec(X)`` or ``Mock(spec=X)``."""
    parts = dotted(node.func)
    if parts[-1:] == ("create_autospec",) and node.args:
        return node.args[0]
    for keyword in node.keywords:
        if keyword.arg in ("spec", "spec_set"):
            return keyword.value
    return None


def first_party_module(ctx: AnalysisContext, qualname: str) -> str | None:
    """The longest source module that prefixes ``qualname``."""
    parts = qualname.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in ctx.files.sources:
            return candidate
    return None


def categories(ctx: AnalysisContext, patch: Patch) -> list[str]:
    """Patch categories: environment, stdlib, external, private, a layer, shared, ..."""
    if patch.environment:
        return ["environment"]
    found: list[str] = []
    target = patch.target
    if (patch.attribute is not None and is_private(patch.attribute)) or (
        target is not None and any(is_private(part) for part in target.split("."))
    ):
        found.append("private")
    if target is None:
        return found
    top = target.split(".", 1)[0]
    if top not in ctx.config.project.root_packages:
        found.append("stdlib" if is_stdlib(target) else "external")
        return found
    found.append("first_party")
    module = first_party_module(ctx, target)
    info = ctx.model.info(module) if module else None
    if info is not None:
        if info.layer is not None:
            found.append(info.layer)
        if info.kind is ModuleKind.SHARED:
            found.append("shared")
        elif info.kind is ModuleKind.COMPOSITION_ROOT:
            found.append("composition_root")
    return found
