from __future__ import annotations

import ast
from collections import Counter
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


class DeprecatedAsyncContextmanagerReturn(Rule):
    file_local = True
    code = "SMT337"
    name = "deprecated-async-contextmanager-return"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="An @asynccontextmanager function annotates its return as AsyncIterator[T].",
        rationale="The contextlib.asynccontextmanager decorator remains supported. Typeshed deprecates its AsyncIterator overload because the decorator consumes an async generator, including its athrow/aclose methods. Use AsyncGenerator[T, None] to express that contract explicitly; the second argument also works on Python 3.12. This opt-in check provides the same guidance without requiring a type checker. It recognizes async functions and methods with direct AsyncIterator[T] annotations from collections.abc or typing, including quoted annotations, module/import aliases and guarded module-level imports such as TYPE_CHECKING. It conservatively skips names rebound anywhere in the file, wildcard imports, local imports, re-exports, user-defined types, type aliases and wrapped annotations. Ordinary async iterators and functions already annotated as AsyncGenerator remain valid. See https://github.com/python/typeshed/blob/main/stdlib/contextlib.pyi for the deprecated overload.",
        bad="from collections.abc import AsyncIterator\nfrom contextlib import asynccontextmanager\n\n@asynccontextmanager\nasync def exclusive() -> AsyncIterator[None]:\n    yield",
        good="from collections.abc import AsyncGenerator\nfrom contextlib import asynccontextmanager\n\n@asynccontextmanager\nasync def exclusive() -> AsyncGenerator[None, None]:\n    yield",
        fix="Replace AsyncIterator[T] with AsyncGenerator[T, None] and import AsyncGenerator from collections.abc. Preserve the yielded type and the decorator, and remove the AsyncIterator import only if unused elsewhere. No automatic rewrite is performed. Enable rules: {deprecated-async-contextmanager-return: warning}, or select SMT337. Package overrides and reasoned inline suppressions are supported.",
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            bindings = _stable_imports(tree)
            for function in ast.walk(tree):
                if not isinstance(function, ast.AsyncFunctionDef) or not any(
                    _qualified(decorator, bindings) == "contextlib.asynccontextmanager"
                    for decorator in function.decorator_list
                ):
                    continue
                annotation = function.returns
                replacement = _replacement(annotation, bindings)
                if replacement is None or annotation is None:
                    continue
                yield self.violation(
                    f"use {replacement} for @asynccontextmanager function {function.name}",
                    path=source.path,
                    line=annotation.lineno,
                    column=annotation.col_offset + 1,
                    end_line=annotation.end_lineno,
                    end_column=(annotation.end_col_offset or 0) + 1,
                    source_module=source.module,
                    expected={
                        "function": function.name,
                        "decorator": "contextlib.asynccontextmanager",
                        "annotation": ast.unparse(annotation),
                        "suggested_annotation": replacement,
                        "suggested_import": "from collections.abc import AsyncGenerator",
                        "automatic_rewrite": False,
                    },
                    hint=f"Annotate as {replacement} and import AsyncGenerator from collections.abc; keep @asynccontextmanager.",
                )


def _replacement(node: ast.expr | None, bindings: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None
    if (
        isinstance(node, ast.Subscript)
        and not isinstance(node.slice, ast.Tuple)
        and _qualified(node.value, bindings)
        in {"collections.abc.AsyncIterator", "typing.AsyncIterator"}
    ):
        return f"AsyncGenerator[{ast.unparse(node.slice)}, None]"
    return None


def _qualified(node: ast.expr, bindings: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.Attribute) and (parent := _qualified(node.value, bindings)):
        return f"{parent}.{node.attr}"
    return None


def _stable_imports(tree: ast.Module) -> dict[str, str]:
    bound = _binding_counts(tree)
    if bound["*"]:
        return {}
    result: dict[str, str] = {}
    for node in _module_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                if bound[local] == 1:
                    result[local] = alias.name if alias.asname else local
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            for alias in node.names:
                local = alias.asname or alias.name
                if bound[local] == 1:
                    result[local] = f"{node.module}.{alias.name}"
    return result


def _binding_counts(tree: ast.Module) -> Counter[str]:
    bound: Counter[str] = Counter()
    for node in ast.walk(tree):
        match node:
            case ast.Name(id=name, ctx=ast.Store() | ast.Del()) | ast.arg(arg=name):
                bound[name] += 1
            case (
                ast.ClassDef(name=name)
                | ast.FunctionDef(name=name)
                | ast.AsyncFunctionDef(name=name)
                | ast.TypeVar(name=name)
                | ast.ParamSpec(name=name)
                | ast.TypeVarTuple(name=name)
                | ast.ExceptHandler(name=name)
                | ast.MatchAs(name=name)
                | ast.MatchStar(name=name)
            ) if name:
                bound[name] += 1
            case ast.Import(names=aliases) | ast.ImportFrom(names=aliases):
                bound.update(
                    alias.asname or alias.name.split(".")[0] for alias in aliases
                )
            case ast.Global(names=names) | ast.Nonlocal(names=names):
                bound.update(names)
            case ast.MatchMapping(rest=name) if name:
                bound[name] += 1
            case (
                ast.Attribute(value=root, ctx=ast.Store() | ast.Del())
                | ast.Subscript(value=root, ctx=ast.Store() | ast.Del())
            ):
                while isinstance(root, ast.Attribute | ast.Subscript):
                    root = root.value
                if isinstance(root, ast.Name):
                    bound[root.id] += 1
    return bound


def _module_nodes(node: ast.AST) -> Iterator[ast.AST]:
    yield node
    if not isinstance(
        node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
    ):
        for child in ast.iter_child_nodes(node):
            yield from _module_nodes(child)
