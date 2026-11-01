"""Bounded visibility candidates for module constants and PEP 695 aliases."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from smelt.analysis.function_usage import explicit_exports

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.members import MemberUsageIndex
    from smelt.analysis.syntax import SyntaxIndex


@dataclass(frozen=True)
class ModuleSymbol:
    module: str
    path: str
    node: ast.Name
    kind: Literal["constant", "type-alias"]
    internal_references: tuple[int, ...]
    occupied_names: frozenset[str]


def internal_module_symbols(
    syntax: SyntaxIndex, usage: MemberUsageIndex
) -> Iterator[ModuleSymbol]:
    for source in sorted(syntax.files.sources.values(), key=lambda item: item.path):
        module = syntax.for_path(source.path)
        if module is None or module.is_package or _reflective(module.tree):
            continue
        exports = explicit_exports(module.tree)
        if exports is None:
            continue
        candidates = list(_candidates(module.tree))
        if not candidates:
            continue
        bindings = _bindings(module.tree)
        loads: dict[str, set[int]] = {}
        for child in ast.walk(module.tree):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                loads.setdefault(child.id, set()).add(child.lineno)
        api = _api_names(module.tree, module.module, exports, usage)
        for target, kind in candidates:
            name = target.id
            if (
                name.startswith("_")
                or bindings[name] != 1
                or name in api
                or usage.module_name_used_externally(module.module, name)
            ):
                continue
            yield ModuleSymbol(
                module.module,
                module.path,
                target,
                kind,
                tuple(sorted(loads.get(name, ()))),
                frozenset(bindings),
            )


def _api_names(
    tree: ast.Module, module: str, exports: frozenset[str], usage: MemberUsageIndex
) -> set[str]:
    api = set(exports)
    for node in tree.body:
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.TypeAlias
        ):
            name = node.name.id if isinstance(node, ast.TypeAlias) else node.name
            if usage.module_name_used_externally(module, name):
                api.add(name)
    for expression in _api_expressions(tree.body, api):
        api.update(_references(expression))
    aliases = {
        node.name.id: node for node in tree.body if isinstance(node, ast.TypeAlias)
    }
    pending = list(api)
    while pending:
        alias = aliases.get(pending.pop())
        if alias is not None:
            dependencies = _references(alias) - api
            api.update(dependencies)
            pending.extend(dependencies)
    return api


def _candidates(
    tree: ast.Module,
) -> Iterator[tuple[ast.Name, Literal["constant", "type-alias"]]]:
    for statement in tree.body:
        if isinstance(statement, ast.TypeAlias):
            yield statement.name, "type-alias"
        else:
            target = None
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
            elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
                target = statement.target
            if isinstance(target, ast.Name) and target.id.isupper():
                yield target, "constant"


def _bindings(tree: ast.Module) -> Counter[str]:
    result: Counter[str] = Counter()
    for node in ast.walk(tree):
        match node:
            case ast.Name(id=name, ctx=ast.Store() | ast.Del()):
                result[name] += 1
            case ast.arg(arg=name):
                result[name] += 1
            case (
                ast.FunctionDef(name=name)
                | ast.AsyncFunctionDef(name=name)
                | ast.ClassDef(name=name)
                | ast.ExceptHandler(name=name)
                | ast.MatchAs(name=name)
                | ast.MatchStar(name=name)
                | ast.MatchMapping(rest=name)
                | ast.TypeVar(name=name)
                | ast.ParamSpec(name=name)
                | ast.TypeVarTuple(name=name)
            ) if name:
                result[name] += 1
            case ast.alias(name=name, asname=alias):
                result[alias or name.split(".")[0]] += 1
    return result


def _reflective(tree: ast.Module) -> bool:
    return any(
        (
            isinstance(node, ast.Name)
            and node.id in {"globals", "locals", "vars", "eval", "exec"}
        )
        or (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name in {"__getattr__", "__dir__"}
        )
        or (
            isinstance(node, ast.ImportFrom)
            and any(alias.name == "*" for alias in node.names)
        )
        or (isinstance(node, ast.alias) and (node.asname or node.name) == "__all__")
        for node in ast.walk(tree)
    )


def _references(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            names.add(child.id)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            try:
                quoted = ast.parse(child.value, mode="eval")
            except (SyntaxError, ValueError):
                continue
            names.update(
                item.id for item in ast.walk(quoted) if isinstance(item, ast.Name)
            )
    return names


def _api_expressions(
    body: list[ast.stmt], exported: set[str], *, in_class: bool = False
) -> Iterator[ast.expr]:
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if (
                node.name.startswith("_")
                and node.name not in exported
                and not (
                    in_class and node.name.startswith("__") and node.name.endswith("__")
                )
            ):
                continue
            yield from _definition_expressions(node)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and not node.target.id.startswith("_")
        ):
            yield node.annotation
            if in_class and node.value is not None:
                yield node.value
        elif (
            in_class
            and isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and not target.id.startswith("_")
                for target in node.targets
            )
        ):
            yield node.value
        elif not isinstance(node, ast.TypeAlias):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.stmt):
                    yield from _api_expressions([child], exported, in_class=in_class)
                elif isinstance(child, ast.ExceptHandler | ast.match_case):
                    yield from _api_expressions(child.body, exported, in_class=in_class)


def _definition_expressions(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> Iterator[ast.expr]:
    yield from node.decorator_list
    for parameter in node.type_params:
        for child in ast.iter_child_nodes(parameter):
            if isinstance(child, ast.expr):
                yield child
    if isinstance(node, ast.ClassDef):
        yield from node.bases
        yield from (keyword.value for keyword in node.keywords)
        yield from _api_expressions(node.body, set(), in_class=True)
        for child in ast.walk(node):
            if (
                isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Attribute)
                and not child.target.attr.startswith("_")
            ):
                yield child.annotation
    else:
        yield from _signature_expressions(node)


def _signature_expressions(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterator[ast.expr]:
    for argument in [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
        *([node.args.vararg] if node.args.vararg else []),
        *([node.args.kwarg] if node.args.kwarg else []),
    ]:
        if argument.annotation is not None:
            yield argument.annotation
    if node.returns is not None:
        yield node.returns
    yield from node.args.defaults
    yield from (value for value in node.args.kw_defaults if value is not None)
