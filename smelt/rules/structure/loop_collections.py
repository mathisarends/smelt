from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.bindings import binding_counts, qualified_name, unshadowed_bindings
from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

type _Shape = str | _Tuple
type _Function = ast.FunctionDef | ast.AsyncFunctionDef
type _Environment = dict[str, _Shape]

_SCALARS = frozenset(
    f"builtins.{name}" for name in ("str", "bytes", "int", "float", "bool", "NoneType")
)
_BUILTINS = (
    "str",
    "bytes",
    "int",
    "float",
    "bool",
    "tuple",
    "ValueError",
    "TypeError",
    "RuntimeError",
)
_ERRORS = frozenset(
    {"builtins.ValueError", "builtins.TypeError", "builtins.RuntimeError"}
)


_VARIADIC_TUPLE_PARTS = 2


class LoopInvariantCollection(Rule):
    file_local = True
    code = "IDM008"
    name = "idioms/loop-invariant-collection"
    category = Category.IDIOMS
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A membership lookup set is rebuilt in a loop from stable immutable inputs.",
        rationale=(
            "Repeated lookup-set construction can turn one validation pass into "
            "quadratic work and obscure a shared domain set. This opt-in review "
            "requires positive, file-local evidence: homogeneous immutable tuple "
            "literals, tuple[T, ...] annotations, or tuple fields of standard-library "
            "frozen dataclasses declared in the same module. Scalar projections "
            "may read directly declared fields of those frozen records. Annotations "
            "are assumed to describe standard builtin values and these concrete "
            "record types; runtime subclass overrides or frozen-state bypasses "
            "are not proved safe. This is not a runtime immutability proof. "
            "Records with bases, extra decorators or custom attribute access are "
            "excluded, as are descriptors, unknown fields and imported record types. "
            "Minimum snapshot evidence is a local @dataclass(frozen=True) record "
            "with a plain id: str field and a tuple[Record, ...] input; the outer "
            "iteration must also be a known tuple. The original Harnessed example "
            "uses Pydantic dataclasses and Annotated fields, which this initial "
            "implementation deliberately does not infer. "
            "Only direct for loops with a simple name target in functions or "
            "ordinary methods are reviewed; while and async loops are not inferred. "
            "The body must consist of passive scalar/tuple expressions, simple "
            "local assignments, conditionals, returns, breaks/continues or literal "
            "builtin error raises. Unknown calls, mutation through aliases, "
            "properties, nested loops/scopes, await/yield and uncertain operations "
            "make it stay quiet. A single-generator set comprehension must produce "
            "known builtin scalar values and appear on the right of in/not in. "
            "Equivalent projections are grouped per loop, including renamed "
            "comprehension variables. Tests are ignored."
        ),
        bad=(
            "def validate(wanted: tuple[str, ...], available: tuple[str, ...]):\n"
            "    for name in wanted:\n"
            "        if name not in {item for item in available}:\n"
            '            raise ValueError("Unknown name")'
        ),
        good=(
            "def validate(wanted: tuple[str, ...], available: tuple[str, ...]):\n"
            "    # Evaluate at the first required check if earlier evaluation matters.\n"
            "    names = set(available)\n"
            "    for name in wanted:\n"
            "        if name not in names:\n"
            '            raise ValueError("Unknown name")'
        ),
        fix=(
            "Review computing the lookup set once and reusing it. Preserve the "
            "empty-loop case, guards, exception timing and evaluation order: "
            "moving construction before a loop or guard can evaluate code that "
            "previously never ran. Keep lazy initialization at the first required "
            "check when necessary. No automatic hoisting is provided. Enable "
            "rules: {idioms/loop-invariant-collection: hint}, or select IDM008."
        ),
        config=("rules", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            bindings = unshadowed_bindings(tree, builtin_names=_BUILTINS)
            facts = _Facts(_records(tree, bindings), bindings)
            for function, owner in _functions(tree):
                for loop, sites in _function_sites(function, owner, facts=facts):
                    first = sites[0]
                    expression = ast.unparse(first)
                    yield self.violation(
                        f"Lookup set {expression} is rebuilt on each iteration from "
                        "stable immutable inputs; review computing it once",
                        path=source.path,
                        line=first.lineno,
                        column=first.col_offset + 1,
                        source_module=source.module,
                        expected={
                            "function": function.name,
                            "loop_line": loop.lineno,
                            "expression": expression,
                            "sites": [
                                {"line": site.lineno, "column": site.col_offset + 1}
                                for site in sites
                            ],
                            "lookup_edges": [
                                f"{owner or '<module>'}.{function.name}: {_lookup_key(first)}"
                            ],
                            "automatic_rewrite": False,
                        },
                        hint="Reuse one lookup set while preserving empty-loop behavior, guards and evaluation order.",
                    )


@dataclass(frozen=True, slots=True)
class _Tuple:
    item: _Shape


@dataclass(frozen=True, slots=True)
class _Facts:
    records: Mapping[str, Mapping[str, ast.expr]]
    bindings: Mapping[str, str]

    def annotation(self, node: ast.expr) -> _Shape | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                node = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return None
        if isinstance(node, ast.Name) and node.id in self.records:
            return node.id
        target = qualified_name(node, self.bindings)
        if target in _SCALARS:
            return target
        if (
            isinstance(node, ast.Subscript)
            and qualified_name(node.value, self.bindings)
            in {"builtins.tuple", "typing.Tuple"}
            and isinstance(node.slice, ast.Tuple)
            and len(node.slice.elts) == _VARIADIC_TUPLE_PARTS
            and isinstance(node.slice.elts[1], ast.Constant)
            and node.slice.elts[1].value is Ellipsis
        ):
            item = self.annotation(node.slice.elts[0])
            return _Tuple(item) if item is not None else None
        return None

    def value(self, node: ast.expr, env: Mapping[str, _Shape]) -> _Shape | None:
        result: _Shape | None = None
        match node:
            case ast.Constant(value=value):
                kind = f"builtins.{type(value).__name__}"
                result = kind if kind in _SCALARS else None
            case ast.Name(id=name):
                result = env.get(name)
            case ast.Attribute(value=parent, attr=name):
                owner = self.value(parent, env)
                field = (
                    self.records.get(owner, {}).get(name)
                    if isinstance(owner, str)
                    else None
                )
                result = self.annotation(field) if field is not None else None
            case ast.Tuple(elts=items) if items:
                kinds = [self.value(item, env) for item in items]
                first = kinds[0]
                result = (
                    _Tuple(first)
                    if first is not None and all(item == first for item in kinds)
                    else None
                )
            case ast.Subscript(value=parent, slice=ast.Constant(value=int())):
                sequence = self.value(parent, env)
                result = sequence.item if isinstance(sequence, _Tuple) else None
            case ast.SetComp():
                result = "builtins.set" if self.lookup(node, env) else None
            case ast.Compare(left=left, comparators=rest):
                result = (
                    "builtins.bool"
                    if all(_passive(self.value(item, env)) for item in [left, *rest])
                    else None
                )
            case ast.BoolOp(values=items):
                result = (
                    "builtins.bool"
                    if all(_passive(self.value(item, env)) for item in items)
                    else None
                )
            case ast.UnaryOp(operand=operand):
                operand_kind = self.value(operand, env)
                result = (
                    operand_kind
                    if isinstance(operand_kind, str) and operand_kind in _SCALARS
                    else None
                )
            case ast.BinOp(left=left, right=right):
                a, b = self.value(left, env), self.value(right, env)
                result = (
                    a
                    if isinstance(a, str)
                    and a in _SCALARS
                    and isinstance(b, str)
                    and b in _SCALARS
                    else None
                )
        return result

    def lookup(self, node: ast.SetComp, env: Mapping[str, _Shape]) -> bool:
        if len(node.generators) != 1:
            return False
        gen = node.generators[0]
        if gen.is_async or gen.ifs or not isinstance(gen.target, ast.Name):
            return False
        source = self.value(gen.iter, env)
        if not isinstance(source, _Tuple):
            return False
        kind = self.value(node.elt, {**env, gen.target.id: source.item})
        return isinstance(kind, str) and kind in _SCALARS


def _functions(tree: ast.Module) -> Iterator[tuple[_Function, str | None]]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node, None
        elif isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
                    yield method, node.name


def _function_sites(
    function: _Function, owner: str | None, *, facts: _Facts
) -> Iterator[tuple[ast.For, list[ast.SetComp]]]:
    if function.decorator_list or any(
        isinstance(node, ast.Await | ast.Yield | ast.YieldFrom)
        for node in ast.walk(function)
    ):
        return
    env: _Environment = {}
    args = [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
    for arg in args:
        kind = facts.annotation(arg.annotation) if arg.annotation else None
        if kind is not None:
            env[arg.arg] = kind
    if owner is not None and owner in facts.records and args:
        env[args[0].arg] = owner
    for statement in function.body:
        if isinstance(statement, ast.For) and isinstance(statement.target, ast.Name):
            for sites in _loop_sites(statement, env, facts=facts):
                yield statement, sites
        if not _assignment(statement, env, facts=facts):
            for name in _writes(statement):
                env.pop(name, None)


def _loop_sites(
    loop: ast.For, env: Mapping[str, _Shape], *, facts: _Facts
) -> Iterator[list[ast.SetComp]]:
    sequence = facts.value(loop.iter, env)
    if not isinstance(sequence, _Tuple) or not isinstance(loop.target, ast.Name):
        return
    local = {**env, loop.target.id: sequence.item}
    candidates: list[ast.SetComp] = []
    if not _body(loop.body, local, facts=facts, found=candidates):
        return
    written = _writes(loop)
    groups: dict[str, list[ast.SetComp]] = defaultdict(list)
    for candidate in candidates:
        if not (_dependencies(candidate) & written):
            groups[_lookup_key(candidate)].append(candidate)
    for sites in groups.values():
        yield sorted(sites, key=lambda site: (site.lineno, site.col_offset))


def _body(
    body: list[ast.stmt], env: _Environment, *, facts: _Facts, found: list[ast.SetComp]
) -> bool:
    for statement in body:
        if isinstance(statement, ast.If):
            yes, no = dict(env), dict(env)
            if (
                not _expression(statement.test, env, facts=facts, found=found)
                or not _body(statement.body, yes, facts=facts, found=found)
                or not _body(statement.orelse, no, facts=facts, found=found)
            ):
                return False
            env.clear()
            env.update(
                {name: kind for name, kind in yes.items() if no.get(name) == kind}
            )
        elif isinstance(statement, ast.Assign | ast.AnnAssign):
            if (
                statement.value is None
                or not _expression(statement.value, env, facts=facts, found=found)
                or not _assignment(statement, env, facts=facts)
            ):
                return False
        elif isinstance(statement, ast.Raise):
            if statement.cause is not None or not _literal_error(
                statement.exc, facts.bindings
            ):
                return False
        elif isinstance(statement, ast.Return):
            if statement.value is not None and not _expression(
                statement.value, env, facts=facts, found=found
            ):
                return False
        elif not isinstance(statement, ast.Pass | ast.Break | ast.Continue):
            return False
    return True


def _expression(
    node: ast.expr,
    env: Mapping[str, _Shape],
    *,
    facts: _Facts,
    found: list[ast.SetComp],
) -> bool:
    if facts.value(node, env) is None:
        return False
    for part in ast.walk(node):
        if isinstance(part, ast.Compare):
            found.extend(
                right
                for operation, right in zip(part.ops, part.comparators, strict=True)
                if isinstance(operation, ast.In | ast.NotIn)
                and isinstance(right, ast.SetComp)
                and facts.lookup(right, env)
            )
    return True


def _assignment(statement: ast.stmt, env: _Environment, *, facts: _Facts) -> bool:
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
        target, value = statement.targets[0], statement.value
    elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
        target, value = statement.target, statement.value
    else:
        return False
    if not isinstance(target, ast.Name):
        return False
    kind = facts.value(value, env)
    if kind is None:
        env.pop(target.id, None)
        return False
    env[target.id] = kind
    return True


def _records(
    tree: ast.Module, bindings: Mapping[str, str]
) -> dict[str, dict[str, ast.expr]]:
    counts = binding_counts(tree)
    result: dict[str, dict[str, ast.expr]] = {}
    for owner in tree.body:
        if (
            not isinstance(owner, ast.ClassDef)
            or owner.bases
            or owner.keywords
            or owner.type_params
            or counts[owner.name] != 1
            or len(owner.decorator_list) != 1
        ):
            continue
        decorator = owner.decorator_list[0]
        if (
            not isinstance(decorator, ast.Call)
            or qualified_name(decorator.func, bindings) != "dataclasses.dataclass"
            or any(keyword.arg is None for keyword in decorator.keywords)
            or not any(
                keyword.arg == "frozen"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in decorator.keywords
            )
        ):
            continue
        methods = {
            node.name
            for node in owner.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        if methods & {"__getattr__", "__getattribute__", "__new__"}:
            continue
        result[owner.name] = _record_fields(owner, methods)
    return result


def _record_fields(owner: ast.ClassDef, methods: set[str]) -> dict[str, ast.expr]:
    statements = [
        node
        for node in owner.body
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]
    counts = binding_counts(ast.Module(body=statements, type_ignores=[]))
    if any(
        not isinstance(node, ast.AnnAssign | ast.Assign)
        and not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        for node in statements
    ):
        return {}
    return {
        field.target.id: field.annotation
        for field in owner.body
        if isinstance(field, ast.AnnAssign)
        and isinstance(field.target, ast.Name)
        and counts[field.target.id] == 1
        and field.target.id not in methods
        and (field.value is None or isinstance(field.value, ast.Constant | ast.Tuple))
    }


def _literal_error(node: ast.expr | None, bindings: Mapping[str, str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and qualified_name(node.func, bindings) in _ERRORS
        and not node.keywords
        and all(isinstance(arg, ast.Constant) for arg in node.args)
    )


def _passive(kind: _Shape | None) -> bool:
    if isinstance(kind, _Tuple):
        return _passive(kind.item)
    return kind in _SCALARS or kind == "builtins.set"


def _writes(node: ast.AST) -> set[str]:
    result: set[str] = set()
    for part in ast.walk(node):
        if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Store | ast.Del):
            result.add(part.id)
        elif isinstance(part, ast.Attribute | ast.Subscript) and isinstance(
            part.ctx, ast.Store | ast.Del
        ):
            parent = part.value
            while isinstance(parent, ast.Attribute | ast.Subscript):
                parent = parent.value
            if isinstance(parent, ast.Name):
                result.add(parent.id)
    return result


def _dependencies(node: ast.SetComp) -> set[str]:
    gen = node.generators[0]
    names = {part.id for part in ast.walk(gen.iter) if isinstance(part, ast.Name)}
    target = gen.target.id if isinstance(gen.target, ast.Name) else ""
    return names | {
        part.id
        for part in ast.walk(node.elt)
        if isinstance(part, ast.Name) and part.id != target
    }


def _lookup_key(node: ast.SetComp) -> str:
    gen = node.generators[0]
    target = gen.target.id if isinstance(gen.target, ast.Name) else ""
    projection = ast.parse(ast.unparse(node.elt), mode="eval")
    for part in ast.walk(projection):
        if isinstance(part, ast.Name) and part.id == target:
            part.id = "<item>"
    return f"{ast.dump(gen.iter)} -> {ast.dump(projection.body)}"
