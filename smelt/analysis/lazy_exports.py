"""One literal lazy-export table and its verified importlib forwarding handler."""

from __future__ import annotations

import ast
from collections import Counter


def static_lazy_exports(tree: ast.Module) -> dict[str, str] | None:
    """None means unsupported/dynamic; a stub or a table alone proves nothing.

    Recognize the guarded table lookup, import_module/getattr, optional globals
    cache and return sequence. Do not interpret arbitrary __getattr__ bodies.
    """
    handlers = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "__getattr__"
    ]
    if len(handlers) != 1:
        return None
    handler = handlers[0]
    imports = _importlib_bindings(tree)
    for statement in tree.body:
        if not isinstance(statement, ast.Assign | ast.AnnAssign) or not isinstance(
            statement.value, ast.Dict
        ):
            continue
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
        )
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            continue
        table = targets[0].id
        entries = _entries(statement.value)
        if entries is None or not _handler(handler, table, imports):
            continue
        if not _stable(tree, handler, table, declaration=statement) or _shadows_exports(
            tree, set(entries)
        ):
            continue
        public = _public_names(tree, table, set(entries))
        if public is not None:
            return {
                name: target
                for name, target in entries.items()
                if name in public and not name.startswith("_")
            }
    return None


_PAIR = 2
_MIN_HANDLER = 4
_CACHED_HANDLER = 5
_DISTINCT_LOCALS = 5


def _entries(node: ast.Dict) -> dict[str, str] | None:
    result: dict[str, str] = {}
    for key, value in zip(node.keys, node.values, strict=True):
        if (
            not isinstance(key, ast.Constant)
            or not isinstance(key.value, str)
            or key.value in result
            or not key.value.isidentifier()
        ):
            return None
        if not isinstance(value, ast.Tuple | ast.List) or len(value.elts) != _PAIR:
            return None
        if not all(
            isinstance(item, ast.Constant) and isinstance(item.value, str)
            for item in value.elts
        ):
            return None
        module, name = (
            item.value for item in value.elts if isinstance(item, ast.Constant)
        )
        if (
            not isinstance(module, str)
            or not isinstance(name, str)
            or not module
            or not all(part.isidentifier() for part in module.split("."))
            or not name.isidentifier()
        ):
            return None
        result[key.value] = f"{module}.{name}"
    return result


def _importlib_bindings(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "importlib"
            and node.level == 0
        ):
            names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "import_module"
            )
        elif isinstance(node, ast.Import):
            names.update(
                f"{alias.asname or alias.name}.import_module"
                for alias in node.names
                if alias.name == "importlib"
            )
    return names


def _handler(handler: ast.FunctionDef, table: str, imports: set[str]) -> bool:
    args = [*handler.args.posonlyargs, *handler.args.args]
    if (
        len(args) != 1
        or handler.decorator_list
        or handler.args.vararg
        or handler.args.kwarg
        or handler.args.kwonlyargs
        or handler.args.defaults
        or handler.type_params
        or any(
            not isinstance(node, ast.Name | ast.Load | ast.Constant)
            for annotation in [
                *(arg.annotation for arg in args if arg.annotation is not None),
                *([handler.returns] if handler.returns is not None else []),
            ]
            for node in ast.walk(annotation)
        )
    ):
        return False
    body = handler.body
    if not _MIN_HANDLER <= len(body) <= _CACHED_HANDLER:
        return False
    guard, lookup, load = body[:3]
    if not _guard(guard, args[0].arg, table):
        return False
    variables = _forwarded_variables(lookup, load)
    if variables is None:
        return False
    module, attribute, value = variables
    local = {args[0].arg, module, attribute, value}
    reserved = {"getattr", "globals", "AttributeError"} | {
        name.split(".")[0] for name in imports
    }
    if len(local | {table}) != _DISTINCT_LOCALS or local & reserved:
        return False
    cache = (
        f"globals()[{args[0].arg}] = {value}\n" if len(body) == _CACHED_HANDLER else ""
    )
    actual = ast.dump(ast.Module(body=body[1:], type_ignores=[]))
    return any(
        actual
        == ast.dump(
            ast.parse(
                f"{module}, {attribute} = {table}[{args[0].arg}]\n{value} = getattr({importer}({module}), {attribute})\n{cache}return {value}"
            )
        )
        for importer in imports
    )


def _guard(guard: ast.stmt, argument: str, table: str) -> bool:
    if (
        not isinstance(guard, ast.If)
        or len(guard.body) != 1
        or guard.orelse
        or not isinstance(guard.body[0], ast.Raise)
        or guard.body[0].cause is not None
    ):
        return False
    exception = guard.body[0].exc
    if (
        not isinstance(exception, ast.Call)
        or not isinstance(exception.func, ast.Name)
        or exception.func.id != "AttributeError"
        or exception.keywords
        or any(
            not isinstance(
                node,
                ast.Constant | ast.Name | ast.Load | ast.JoinedStr | ast.FormattedValue,
            )
            for arg in exception.args
            for node in ast.walk(arg)
        )
        or any(
            isinstance(node, ast.Name) and node.id not in {argument, "__name__"}
            for arg in exception.args
            for node in ast.walk(arg)
        )
    ):
        return False
    return ast.dump(guard.test) == ast.dump(
        ast.parse(f"{argument} not in {table}", mode="eval").body
    )


def _forwarded_variables(
    lookup: ast.stmt, load: ast.stmt
) -> tuple[str, str, str] | None:
    if (
        not isinstance(lookup, ast.Assign)
        or len(lookup.targets) != 1
        or not isinstance(lookup.targets[0], ast.Tuple)
        or len(lookup.targets[0].elts) != _PAIR
        or not all(isinstance(node, ast.Name) for node in lookup.targets[0].elts)
    ):
        return None
    module, attribute = (
        node.id for node in lookup.targets[0].elts if isinstance(node, ast.Name)
    )
    if (
        not isinstance(load, ast.Assign)
        or len(load.targets) != 1
        or not isinstance(load.targets[0], ast.Name)
    ):
        return None
    value = load.targets[0].id
    return module, attribute, value


def _stable(
    tree: ast.Module, handler: ast.FunctionDef, table: str, *, declaration: ast.stmt
) -> bool:
    writes: Counter[str] = Counter()
    imports: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            writes[node.id] += 1
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            writes[node.name] += 1
        elif isinstance(node, ast.Import | ast.ImportFrom):
            imports.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.Global | ast.Nonlocal):
            return False
    if (
        writes[table] != 1
        or writes["__getattr__"] != 1
        or imports[table]
        or any(
            writes[name] or imports[name]
            for name in (
                "getattr",
                "globals",
                "AttributeError",
                "sorted",
                "list",
                "tuple",
                "set",
            )
        )
    ):
        return False
    if any(
        writes[name.split(".")[0]] or imports[name.split(".")[0]] != 1
        for name in _importlib_bindings(tree)
    ):
        return False
    if writes["__all__"] > 1:
        return False
    allowed = _allowed_references(tree, handler, table, declaration=declaration)
    return _references_stable(tree, table, allowed)


def _references_stable(tree: ast.Module, table: str, allowed: set[ast.AST]) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id in {table, "__all__"}
            and node not in allowed
        ):
            return False
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"exec", "eval", "globals", "locals", "vars"}
            and node not in allowed
        ):
            return False
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store | ast.Del)
            and isinstance(node.value, ast.Name)
            and any(
                name.split(".")[0] == node.value.id
                for name in _importlib_bindings(tree)
            )
        ):
            return False
    return True


def _allowed_references(
    tree: ast.Module, handler: ast.FunctionDef, table: str, *, declaration: ast.stmt
) -> set[ast.AST]:
    allowed = set(ast.walk(handler)) | set(ast.walk(declaration))
    for statement in tree.body:
        if isinstance(statement, ast.FunctionDef) and statement.name == "__dir__":
            # Only the standard side-effect-free discovery expression is supported.
            expected = ast.parse(f"return sorted(set(globals()) | set({table}))").body
            if (
                not statement.decorator_list
                and not [
                    *statement.args.posonlyargs,
                    *statement.args.args,
                    *statement.args.kwonlyargs,
                ]
                and statement.args.vararg is None
                and statement.args.kwarg is None
                and ast.dump(ast.Module(body=statement.body, type_ignores=[]))
                == ast.dump(ast.Module(body=expected, type_ignores=[]))
            ):
                allowed.update(
                    node for body in statement.body for node in ast.walk(body)
                )
        elif isinstance(statement, ast.Assign | ast.AnnAssign):
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            if any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in targets
            ):
                allowed.update(ast.walk(statement))
    return allowed


def _shadows_exports(tree: ast.Module, names: set[str]) -> bool:
    return any(
        (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store | ast.Del)
            and node.id in names
        )
        or (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            and node.name in names
        )
        or (
            isinstance(node, ast.Import | ast.ImportFrom)
            and any(
                alias.name == "*" or (alias.asname or alias.name.split(".")[0]) in names
                for alias in node.names
            )
        )
        for node in ast.walk(tree)
    )


def _public_names(tree: ast.Module, table: str, names: set[str]) -> set[str] | None:
    declarations: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if (
                any(
                    isinstance(target, ast.Name) and target.id == "__all__"
                    for target in targets
                )
                and node.value is not None
            ):
                if len(targets) != 1:
                    return None
                declarations.append(node.value)
    if not declarations:
        return names
    if len(declarations) != 1:
        return None
    value = declarations[0]
    if any(
        ast.dump(value) == ast.dump(ast.parse(f"{function}({table})", mode="eval").body)
        for function in ("sorted", "list", "tuple")
    ):
        return names
    if isinstance(value, ast.List | ast.Tuple | ast.Set) and all(
        isinstance(item, ast.Constant) and isinstance(item.value, str)
        for item in value.elts
    ):
        return {
            item.value
            for item in value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        } & names
    return None
