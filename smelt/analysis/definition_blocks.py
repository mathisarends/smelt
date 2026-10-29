"""Statements that can be considered together without executing source code."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smelt.config.models import OrderingConfig

type ScopeNode = ast.Module | ast.ClassDef
type FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef

_TYPE_FACTORIES = frozenset(
    {
        f"{root}.{name}"
        for root in ("typing", "typing_extensions")
        for name in ("TypeVar", "TypeVarTuple", "ParamSpec")
    }
)
_OVERLOAD = frozenset({"typing.overload", "typing_extensions.overload"})
_WRAPPERS = frozenset(
    {"builtins.property", "builtins.staticmethod", "builtins.classmethod"}
)
_INIT = frozenset({"__init__", "__new__", "__post_init__"})


@dataclass(frozen=True)
class DefinitionBlock:
    nodes: tuple[ast.stmt, ...]
    first: int
    stop: int
    label: str
    key: str
    category: str
    binds: frozenset[str]
    reads: frozenset[str]
    barrier: str | None = None
    fix_reason: str | None = None

    @property
    def line(self) -> int:
        return min(statement_line(node) for node in self.nodes)

    @property
    def end_line(self) -> int:
        return max(node.end_lineno or node.lineno for node in self.nodes)


def statement_line(node: ast.stmt) -> int:
    decorators = (
        node.decorator_list
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        else []
    )
    return min([node.lineno, *(decorator.lineno for decorator in decorators)])


def _extra_bound_names(node: ast.stmt) -> frozenset[str]:
    if isinstance(node, ast.AugAssign):
        return (
            frozenset({node.target.id})
            if isinstance(node.target, ast.Name)
            else frozenset()
        )
    if isinstance(node, ast.TypeAlias):
        return frozenset({node.name.id})
    if isinstance(node, ast.Delete):
        return frozenset(
            child.id
            for target in node.targets
            for child in ast.walk(target)
            if isinstance(child, ast.Name)
        )
    return frozenset()


def bound_names(node: ast.stmt) -> frozenset[str]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return frozenset({node.name})
    if isinstance(node, ast.Assign):
        return frozenset(
            child.id
            for target in node.targets
            for child in ast.walk(target)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
        )
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.value is not None
    ):
        return frozenset({node.target.id})
    if isinstance(node, ast.Import | ast.ImportFrom):
        return frozenset(
            alias.asname
            or (
                alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name
            )
            for alias in node.names
            if alias.name != "*"
        )
    return _extra_bound_names(node)


def _forget_conditional_aliases(node: ast.stmt, aliases: dict[str, str]) -> None:
    for child in ast.walk(node):
        if isinstance(child, ast.stmt):
            for name in bound_names(child):
                aliases.pop(name, None)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store | ast.Del):
            aliases.pop(child.id, None)
        if isinstance(child, ast.ImportFrom) and any(
            alias.name == "*" for alias in child.names
        ):
            aliases.clear()


def _namespace_mutation(node: ast.stmt) -> bool:
    targets = (
        node.targets
        if isinstance(node, ast.Assign | ast.Delete)
        else [node.target]
        if isinstance(node, ast.AnnAssign | ast.AugAssign)
        else []
    )
    return any(
        isinstance(child, ast.Attribute | ast.Subscript)
        for target in targets
        for child in ast.walk(target)
    )


def update_aliases(node: ast.stmt, aliases: dict[str, str]) -> None:
    for name in bound_names(node):
        aliases.pop(name, None)
    if _namespace_mutation(node):
        aliases.clear()
        return
    for expression in eager_expressions(node, deferred=False):
        for child in ast.walk(expression):
            if isinstance(child, ast.NamedExpr) and isinstance(child.target, ast.Name):
                aliases.pop(child.target.id, None)
    if isinstance(node, ast.ImportFrom) and any(
        alias.name == "*" for alias in node.names
    ):
        aliases.clear()
        return
    if isinstance(node, ast.Import):
        for alias in node.names:
            aliases[alias.asname or alias.name.split(".")[0]] = (
                alias.name if alias.asname else alias.name.split(".")[0]
            )
    elif isinstance(node, ast.ImportFrom) and node.level == 0:
        for alias in node.names:
            if alias.name != "*":
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    elif isinstance(
        node,
        ast.If | ast.Try | ast.TryStar | ast.With | ast.For | ast.While | ast.Match,
    ):
        _forget_conditional_aliases(node, aliases)


def qualified_name(node: ast.expr, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        parent = qualified_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else None
    return None


def is_main_guard(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(right, ast.Constant)
        and right.value == "__main__"
    ) or (
        isinstance(right, ast.Name)
        and right.id == "__name__"
        and isinstance(left, ast.Constant)
        and left.value == "__main__"
    )


def eager_expressions(node: ast.stmt, *, deferred: bool) -> list[ast.expr]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        args = node.args
        found = [
            *node.decorator_list,
            *args.defaults,
            *(item for item in args.kw_defaults if item is not None),
        ]
        if not deferred:
            parameters = [
                *args.posonlyargs,
                *args.args,
                *args.kwonlyargs,
                *([args.vararg] if args.vararg else []),
                *([args.kwarg] if args.kwarg else []),
            ]
            found.extend(
                arg.annotation for arg in parameters if arg.annotation is not None
            )
            if node.returns is not None:
                found.append(node.returns)
        return found
    if isinstance(node, ast.ClassDef):
        return [
            *node.decorator_list,
            *node.bases,
            *(item.value for item in node.keywords),
        ]
    if isinstance(node, ast.Assign):
        return [node.value]
    if isinstance(node, ast.AnnAssign):
        return [
            *([node.value] if node.value is not None else []),
            *([node.annotation] if not deferred else []),
        ]
    return []


class _Reads(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.names.add(node.id)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for value in [
            *node.args.defaults,
            *(item for item in node.args.kw_defaults if item is not None),
        ]:
            self.visit(value)


def expression_reads(expressions: list[ast.expr]) -> frozenset[str]:
    visitor = _Reads()
    for expression in expressions:
        visitor.visit(expression)
    return frozenset(visitor.names)


def definition_reads(node: ast.stmt, *, deferred: bool) -> frozenset[str]:
    reads = set(expression_reads(eager_expressions(node, deferred=deferred)))
    if isinstance(node, ast.ClassDef):
        local: set[str] = set()
        for child in node.body:
            reads.update(definition_reads(child, deferred=deferred) - local)
            local.update(bound_names(child))
    return frozenset(reads)


def pure_expression(node: ast.expr) -> bool:
    if isinstance(node, ast.Name | ast.Constant):
        return True
    if isinstance(node, ast.Tuple | ast.List):
        return all(pure_expression(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(isinstance(key, ast.Constant) for key in node.keys) and all(
            pure_expression(value) for value in node.values
        )
    if isinstance(node, ast.Set):
        return all(isinstance(item, ast.Constant) for item in node.elts)
    if isinstance(node, ast.Lambda):
        return all(
            pure_expression(item)
            for item in [
                *node.args.defaults,
                *(value for value in node.args.kw_defaults if value is not None),
            ]
        )
    return False


def _wrapper(node: ast.expr, function: FunctionNode, aliases: dict[str, str]) -> bool:
    if qualified_name(node, aliases) in _OVERLOAD | _WRAPPERS:
        return True
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == function.name
        and aliases.get(function.name) == "builtins.property-instance"
        and node.attr in {"getter", "setter", "deleter"}
    )


def _safe_function(
    node: FunctionNode, aliases: dict[str, str], *, deferred: bool
) -> str | None:
    if node.type_params:
        return "generic type parameters are an execution boundary"
    if not all(_wrapper(decorator, node, aliases) for decorator in node.decorator_list):
        return "unrecognized decorators execute at definition time"
    expressions = eager_expressions(node, deferred=deferred)[len(node.decorator_list) :]
    return (
        None
        if all(pure_expression(value) for value in expressions)
        else "default or eager annotation evaluation may execute code"
    )


def _assignment_name(node: ast.stmt) -> str | None:
    target = (
        node.targets[0]
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        else node.target
        if isinstance(node, ast.AnnAssign)
        else None
    )
    return target.id if isinstance(target, ast.Name) else None


def _type_factory(node: ast.stmt, aliases: dict[str, str]) -> bool:
    return (
        isinstance(node, ast.Assign | ast.AnnAssign)
        and isinstance(node.value, ast.Call)
        and qualified_name(node.value.func, aliases) in _TYPE_FACTORIES
    )


def _visibility(name: str) -> str:
    return (
        "private"
        if name.startswith("_") and not (name.startswith("__") and name.endswith("__"))
        else "public"
    )


def _assignment_category(
    node: ast.Assign | ast.AnnAssign,
    *,
    in_class: bool,
    config: OrderingConfig,
    aliases: dict[str, str],
) -> str:
    name = _assignment_name(node)
    if in_class:
        return "class-attributes"
    if name and name.startswith("__") and name.endswith("__"):
        return "dunder-assignments"
    if _type_factory(node, aliases):
        return "type-vars"
    if name and config.constants_exempt and name.lstrip("_").isupper():
        return "public"
    return _visibility(name or "_")


def _definition_category(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, *, in_class: bool
) -> str:
    if in_class and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        if node.name in _INIT:
            return "init"
        if node.name.startswith("__") and node.name.endswith("__"):
            return "dunders"
    return _visibility(node.name)


def _category(
    node: ast.stmt, *, in_class: bool, config: OrderingConfig, aliases: dict[str, str]
) -> str:
    if (
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and node.level == 0
    ):
        return "future-imports"
    if isinstance(node, ast.Import | ast.ImportFrom):
        return "imports"
    if is_main_guard(node):
        return "main-guard"
    if isinstance(node, ast.Assign | ast.AnnAssign):
        return _assignment_category(
            node, in_class=in_class, config=config, aliases=aliases
        )
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return _definition_category(node, in_class=in_class)
    return "barrier"


def _class_barrier(
    node: ast.ClassDef, aliases: dict[str, str], *, deferred: bool
) -> str | None:
    if node.bases or node.keywords or node.decorator_list or node.type_params:
        return "class creation may execute bases, decorators or metaclass behavior"
    for child in node.body:
        if isinstance(child, ast.ClassDef):
            return "nested class creation is an execution boundary"
        if isinstance(child, ast.Pass) or (
            isinstance(child, ast.Expr)
            and isinstance(child.value, ast.Constant)
            and isinstance(child.value.value, str)
        ):
            continue
        if _barrier(child, aliases, deferred=deferred) is not None:
            return "class body contains an execution boundary"
    return None


def _barrier(node: ast.stmt, aliases: dict[str, str], *, deferred: bool) -> str | None:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return _safe_function(node, aliases, deferred=deferred)
    if isinstance(node, ast.ClassDef):
        return _class_barrier(node, aliases, deferred=deferred)
    if isinstance(node, ast.Assign | ast.AnnAssign):
        if _assignment_name(node) is None:
            return "mutation or multiple assignment targets"
        if _type_factory(node, aliases) and isinstance(node.value, ast.Call):
            call = node.value
            safe = all(pure_expression(value) for value in call.args) and all(
                item.arg is not None and pure_expression(item.value)
                for item in call.keywords
            )
            return None if safe else "type-variable arguments may execute code"
        return (
            None
            if all(
                pure_expression(value)
                for value in eager_expressions(node, deferred=deferred)
            )
            else "assignment may execute calls, operators or protocols"
        )
    return "import, control flow, registration or other executable statement"


def _family(body: list[ast.stmt], first: int) -> list[ast.stmt]:
    node = body[first]
    group = [node]
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        for following in body[first + 1 :]:
            if (
                not isinstance(following, ast.FunctionDef | ast.AsyncFunctionDef)
                or following.name != node.name
            ):
                break
            group.append(following)
    return group


def _factory_fix_reason(node: ast.stmt, aliases: dict[str, str]) -> str | None:
    if (
        not _type_factory(node, aliases)
        or not isinstance(node, ast.Assign | ast.AnnAssign)
        or not isinstance(node.value, ast.Call)
    ):
        return None
    literal = all(isinstance(value, ast.Constant) for value in node.value.args) and all(
        isinstance(item.value, ast.Constant) for item in node.value.keywords
    )
    return None if literal else "nonliteral type-variable construction requires review"


def build_blocks(
    owner: ScopeNode,
    *,
    scope: str,
    config: OrderingConfig,
    aliases: dict[str, str],
    deferred: bool,
) -> tuple[list[DefinitionBlock], list[tuple[ast.ClassDef, dict[str, str]]]]:
    body = owner.body
    in_class = isinstance(owner, ast.ClassDef)
    blocks: list[DefinitionBlock] = []
    children: list[tuple[ast.ClassDef, dict[str, str]]] = []
    counts: Counter[str] = Counter()
    index = 0
    while index < len(body):
        first = index
        node = body[index]
        group = _family(body, first)
        index += len(group) - 1
        if isinstance(node, ast.ClassDef):
            children.append((node, dict(aliases)))
        category = (
            "docstring"
            if first == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            else _category(node, in_class=in_class, config=config, aliases=aliases)
        )
        label = (
            node.name
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
            else _assignment_name(node) or ast.unparse(node).splitlines()[0]
        )
        key = f"{scope}.{label}#{counts[label]}"
        counts[label] += 1
        binds: set[str] = set()
        reads: set[str] = set()
        reason = None
        member_aliases = dict(aliases)
        for member in group:
            reads.update(definition_reads(member, deferred=deferred) - binds)
            binds.update(bound_names(member))
            reason = reason or _barrier(member, member_aliases, deferred=deferred)
            property_member = isinstance(
                member, ast.FunctionDef | ast.AsyncFunctionDef
            ) and any(
                qualified_name(decorator, member_aliases) == "builtins.property"
                or (
                    isinstance(decorator, ast.Attribute)
                    and isinstance(decorator.value, ast.Name)
                    and decorator.value.id == member.name
                    and member_aliases.get(member.name) == "builtins.property-instance"
                )
                for decorator in member.decorator_list
            )
            update_aliases(member, member_aliases)
            if property_member and isinstance(
                member, ast.FunctionDef | ast.AsyncFunctionDef
            ):
                member_aliases[member.name] = "builtins.property-instance"
        if in_class and isinstance(node, ast.ClassDef):
            reason = "nested class creation is an execution boundary"
        if category == "docstring":
            reason = "docstring position is fixed"
        if (
            first > 0
            and (body[first - 1].end_lineno or body[first - 1].lineno)
            >= statement_line(node)
        ) or (
            index + 1 < len(body)
            and (group[-1].end_lineno or group[-1].lineno)
            >= statement_line(body[index + 1])
        ):
            reason = "statements share a physical line"
        fix_reason = _factory_fix_reason(node, aliases)
        blocks.append(
            DefinitionBlock(
                tuple(group),
                first,
                index + 1,
                label,
                key,
                category,
                frozenset(binds),
                frozenset(reads),
                reason,
                fix_reason,
            )
        )
        aliases.clear()
        aliases.update(member_aliases)
        index += 1
    return blocks, children
