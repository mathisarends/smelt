"""Static evidence for data and module namespaces, without importing analyzed code."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.exports import MAX_HOPS, resolve_dotted

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import FileIndex
    from smelt.analysis.syntax import ModuleSyntax, SyntaxIndex

_VALUES = frozenset(
    {
        "builtins.str",
        "builtins.bytes",
        "builtins.list",
        "builtins.dict",
        "builtins.set",
        "builtins.frozenset",
        "builtins.tuple",
        "collections.abc.Mapping",
        "collections.abc.MutableMapping",
        "collections.abc.Sequence",
        "collections.abc.MutableSequence",
        "collections.abc.Set",
        "collections.abc.MutableSet",
        "pathlib.Path",
        "pathlib.PurePath",
        "pathlib.PosixPath",
        "pathlib.WindowsPath",
        "io.IOBase",
    }
)
_ALIASES = {
    "typing.List": "builtins.list",
    "typing.Dict": "builtins.dict",
    "typing.Set": "builtins.set",
    "typing.FrozenSet": "builtins.frozenset",
    "typing.Tuple": "builtins.tuple",
    "typing.Mapping": "collections.abc.Mapping",
    "typing.MutableMapping": "collections.abc.MutableMapping",
    "typing.Sequence": "collections.abc.Sequence",
}


@dataclass(frozen=True)
class _Type:
    name: str
    fields: dict[str, _Type | None] | None = None


class NavigationNamespaces:
    def __init__(self, syntax: SyntaxIndex, files: FileIndex) -> None:
        self._syntax = syntax
        self._files = files
        self._definitions: dict[str, tuple[str, ast.ClassDef] | None] = {}
        self._records: dict[str, bool] = {}
        self._owners: dict[tuple[str, int], dict[str, _Type | None]] = {}

    def contains(
        self, module: str, owner: ast.ClassDef, attributes: tuple[str, ...]
    ) -> bool:
        key = module, owner.lineno
        if key not in self._owners:
            self._owners[key] = self._instance_fields(module, owner)
        value = self._owners[key].get(attributes[0])
        for attribute in attributes[1:]:
            if value is None or not self._namespace(value):
                return False
            value = self._attribute(value, attribute)
        return value is not None and self._namespace(value)

    def _qualified(self, module: str, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            namespace = self._syntax.namespace(module)
            if namespace is None:
                return None
            if node.id in namespace.imported:
                return namespace.imported[node.id]
            if node.id in namespace.defined:
                return f"{module}.{node.id}"
            if f"builtins.{node.id}" in _VALUES | {"builtins.property"}:
                return f"builtins.{node.id}"
        if isinstance(node, ast.Attribute):
            parent = self._qualified(module, node.value)
            return f"{parent}.{node.attr}" if parent else None
        return None

    def _annotation(self, module: str, node: ast.AST) -> _Type | None:  # noqa: PLR0911 - bounded annotation syntax
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                return self._annotation(module, ast.parse(node.value, mode="eval").body)
            except SyntaxError:
                return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            if isinstance(node.right, ast.Constant) and node.right.value is None:
                return self._annotation(module, node.left)
            if isinstance(node.left, ast.Constant) and node.left.value is None:
                return self._annotation(module, node.right)
            return None
        if isinstance(node, ast.Subscript):
            origin = self._qualified(module, node.value)
            if origin in {"typing.Optional", "typing.Annotated"}:
                argument = (
                    node.slice.elts[0]
                    if isinstance(node.slice, ast.Tuple)
                    else node.slice
                )
                return self._annotation(module, argument)
            return self._annotation(module, node.value)
        name = self._qualified(module, node)
        return _Type(_ALIASES.get(name, name)) if name else None

    def _definition(self, name: str) -> tuple[str, ast.ClassDef] | None:
        if name not in self._definitions:
            self._definitions[name] = None
            resolved = resolve_dotted(name, self._syntax.namespace)
            if resolved is not None:
                module, symbol = resolved
                syntax = self._module(module)
                if syntax is not None:
                    node = next(
                        (
                            node
                            for node in syntax.tree.body
                            if isinstance(node, ast.ClassDef) and node.name == symbol
                        ),
                        None,
                    )
                    if node is not None:
                        self._definitions[name] = module, node
        return self._definitions[name]

    def _module(self, module: str) -> ModuleSyntax | None:
        source = self._files.sources.get(module)
        return self._syntax.for_path(source.path) if source else None

    def _record(self, name: str) -> bool:
        if name == "pydantic.BaseModel":
            return True
        if name not in self._records:
            self._records[name] = False
            definition = self._definition(name)
            if definition is not None:
                module, node = definition
                self._records[name] = any(
                    self._qualified(
                        module,
                        decorator.func
                        if isinstance(decorator, ast.Call)
                        else decorator,
                    )
                    == "dataclasses.dataclass"
                    for decorator in node.decorator_list
                ) or any(
                    self._record(base_type.name)
                    for base in node.bases
                    if (base_type := self._annotation(module, base)) is not None
                )
        return self._records[name]

    def _namespace(self, value: _Type) -> bool:
        return value.name in _VALUES | {
            "types.ModuleType",
            "types.SimpleNamespace",
            "subprocess.Popen",
        } or self._record(value.name)

    def _attribute(  # noqa: PLR0911 - namespace kinds have distinct static evidence
        self, value: _Type, attribute: str, *, depth: int = 0
    ) -> _Type | None:
        if depth > MAX_HOPS:
            return None
        if value.fields is not None:
            return value.fields.get(attribute)
        if value.name == "types.ModuleType":
            return value
        if value.name.startswith("pathlib.") and attribute == "parent":
            return value
        if value.name == "subprocess.Popen" and attribute in {
            "stdin",
            "stdout",
            "stderr",
        }:
            return _Type("io.IOBase")
        definition = self._definition(value.name)
        if definition is None:
            return None
        module, node = definition
        for statement in node.body:
            if (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and statement.target.id == attribute
            ):
                return self._annotation(module, statement.annotation)
        for base in node.bases:
            base_type = self._annotation(module, base)
            if base_type is not None:
                result = self._attribute(base_type, attribute, depth=depth + 1)
                if result is not None:
                    return result
        return None

    def _instance_fields(  # noqa: PLR0912 - distinguish declarations, constructor writes and rebinding
        self, module: str, owner: ast.ClassDef
    ) -> dict[str, _Type | None]:
        declared: dict[str, _Type | None] = {}
        inferred: dict[str, _Type | None] = {}
        for statement in owner.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(
                statement.target, ast.Name
            ):
                declared[statement.target.id] = self._annotation(
                    module, statement.annotation
                )
            if not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            args = [
                *statement.args.posonlyargs,
                *statement.args.args,
                *statement.args.kwonlyargs,
            ]
            if not args:
                continue
            receiver = args[0].arg
            parameters = {
                arg.arg: self._annotation(module, arg.annotation)
                if arg.annotation
                else None
                for arg in args
            }
            nodes = list(_body_nodes(statement))
            shadowed = {
                node.id
                for node in nodes
                if isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Store | ast.Del)
            }
            if (
                statement.name != "__init__"
                and statement.returns
                and any(
                    self._qualified(module, decorator)
                    in {"builtins.property", "functools.cached_property"}
                    for decorator in statement.decorator_list
                )
            ):
                declared[statement.name] = self._annotation(module, statement.returns)
            if (
                statement.decorator_list
                or statement.name
                in {"__new__", "__init_subclass__", "__class_getitem__"}
                or receiver in shadowed
            ):
                continue
            for node in nodes:
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                    if isinstance(node, ast.AnnAssign | ast.AugAssign)
                    else []
                )
                for target in targets:
                    if (
                        not isinstance(target, ast.Attribute)
                        or not isinstance(target.value, ast.Name)
                        or target.value.id != receiver
                    ):
                        continue
                    if isinstance(node, ast.AnnAssign):
                        declared[target.attr] = self._annotation(
                            module, node.annotation
                        )
                    elif isinstance(node, ast.Assign):
                        value = (
                            self._expression(module, node.value, parameters, shadowed)
                            if statement.name == "__init__"
                            else None
                        )
                        if target.attr in inferred and inferred[target.attr] != value:
                            inferred[target.attr] = None
                        else:
                            inferred[target.attr] = value
                    else:
                        inferred[target.attr] = None
        return inferred | declared

    def _expression(  # noqa: PLR0911 - only explicit constructor and module evidence is accepted
        self,
        module: str,
        node: ast.expr,
        parameters: dict[str, _Type | None],
        shadowed: set[str],
    ) -> _Type | None:
        if isinstance(node, ast.Name):
            if node.id in shadowed:
                return None
            if node.id in parameters:
                return parameters[node.id]
            syntax = self._module(module)
            if syntax is not None:
                for statement in syntax.tree.body:
                    if isinstance(statement, ast.Import) and any(
                        (alias.asname or alias.name.split(".")[0]) == node.id
                        for alias in statement.names
                    ):
                        namespace = self._syntax.namespace(module)
                        if namespace and any(
                            namespace.imported.get(node.id)
                            == (
                                alias.name if alias.asname else alias.name.split(".")[0]
                            )
                            for alias in statement.names
                            if (alias.asname or alias.name.split(".")[0]) == node.id
                        ):
                            return _Type("types.ModuleType")
        if isinstance(node, ast.Call):
            if any(
                isinstance(part, ast.Name) and part.id in shadowed | parameters.keys()
                for part in ast.walk(node.func)
            ):
                return None
            qualified = self._qualified(module, node.func)
            if qualified == "types.SimpleNamespace":
                if any(keyword.arg is None for keyword in node.keywords):
                    return None
                return _Type(
                    qualified,
                    {
                        keyword.arg: self._expression(
                            module, keyword.value, parameters, shadowed
                        )
                        for keyword in node.keywords
                        if keyword.arg
                    },
                )
            if qualified:
                return _Type(qualified)
        return None


def _body_nodes(method: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.AST]:
    pending: list[ast.AST] = list(method.body)
    while pending:
        node = pending.pop()
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
        ):
            continue
        yield node
        pending.extend(ast.iter_child_nodes(node))
