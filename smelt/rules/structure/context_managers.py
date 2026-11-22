from __future__ import annotations

import ast
from collections import Counter
from typing import TYPE_CHECKING

from smelt.analysis.bindings import qualified_name
from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import tree_nodes
from smelt.diagnostics.fixes import Applicability, Fix, FixAvailability, TextEdit
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.edits import (
    SourceText,
    add_from_import,
    name_used,
    remove_import,
    top_level_import,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping


class DeprecatedAsyncContextmanagerReturn(Rule):
    file_local = True
    code = "TYP004"
    name = "typing/deprecated-async-contextmanager-return"
    category = Category.TYPING
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="An @asynccontextmanager function annotates its return as AsyncIterator[T].",
        rationale="The contextlib.asynccontextmanager decorator remains supported. Typeshed deprecates its AsyncIterator overload because the decorator consumes an async generator, including its athrow/aclose methods. Use AsyncGenerator[T, None] to express that contract explicitly; the second argument also works on Python 3.12. This opt-in check provides the same guidance without requiring a type checker. It recognizes async functions and methods with direct AsyncIterator[T] annotations from collections.abc or typing, including quoted annotations, module/import aliases and guarded module-level imports such as TYPE_CHECKING. It conservatively skips names rebound anywhere in the file, wildcard imports, local imports, re-exports, user-defined types, type aliases and wrapped annotations. Ordinary async iterators and functions already annotated as AsyncGenerator remain valid. See https://github.com/python/typeshed/blob/main/stdlib/contextlib.pyi for the deprecated overload.",
        bad="from collections.abc import AsyncIterator\nfrom contextlib import asynccontextmanager\n\n@asynccontextmanager\nasync def exclusive() -> AsyncIterator[None]:\n    yield",
        good="from collections.abc import AsyncGenerator\nfrom contextlib import asynccontextmanager\n\n@asynccontextmanager\nasync def exclusive() -> AsyncGenerator[None, None]:\n    yield",
        fix="Replace AsyncIterator[T] with AsyncGenerator[T, None] and import AsyncGenerator from collections.abc. Preserve the yielded type and the decorator, and remove the AsyncIterator import only if unused elsewhere. check --fix rewrites the annotation, reuses or adds the AsyncGenerator import from the same module and removes a module-level AsyncIterator import that nothing else names. Enable rules: {typing/deprecated-async-contextmanager-return: warning}, or select TYP004. Package overrides and reasoned inline suppressions are supported.",
        canonical=(
            "Annotate @asynccontextmanager functions as AsyncGenerator[T, None]"
        ),
        config=("rules", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            bindings = _stable_imports(tree)
            text = SourceText(ctx.files.read_text(source.path))
            for function in tree_nodes(tree):
                if not isinstance(function, ast.AsyncFunctionDef) or not any(
                    qualified_name(decorator, bindings)
                    == "contextlib.asynccontextmanager"
                    for decorator in function.decorator_list
                ):
                    continue
                annotation = function.returns
                replacement = _replacement(annotation, bindings)
                if replacement is None or annotation is None:
                    continue
                fix = _fix(text, tree, annotation=annotation, bindings=bindings)
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
                        "automatic_rewrite": fix is not None,
                    },
                    hint=f"Annotate as {replacement} and import AsyncGenerator from collections.abc; keep @asynccontextmanager.",
                    fix=fix,
                )


def _fix(
    source: SourceText,
    tree: ast.Module,
    *,
    annotation: ast.expr,
    bindings: Mapping[str, str],
) -> Fix | None:
    """Annotate as AsyncGenerator[T, None], importing it like AsyncIterator.

    Safe: the decorator's contract is unchanged and only the annotation differs.
    """
    node = _parsed(annotation)
    if not isinstance(node, ast.Subscript):
        return None
    edits: list[TextEdit] = []
    match node.value:
        case ast.Attribute(value=owner):
            # typing.AsyncIterator and abc.AsyncIterator name their module already.
            spelling: str | None = f"{ast.unparse(owner)}.AsyncGenerator"
        case ast.Name() as local:
            spelling = _generator_name(
                source,
                tree,
                local=local.id,
                context=(annotation, bindings),
                edits=edits,
            )
        case _:
            spelling = None
    if spelling is None:
        return None
    if isinstance(annotation, ast.Constant):
        quote = source.segment(annotation)[0]
        value = f"{spelling}[{ast.unparse(node.slice)}, None]"
        edits.append(source.replace(annotation, f"{quote}{value}{quote}"))
    else:
        edits.append(source.replace(node.value, spelling))
        edits.append(source.insert(source.span(node.slice)[1], ", None"))
    return Fix(
        Applicability.SAFE,
        "Annotate the context manager as AsyncGenerator[T, None]",
        tuple(edits),
    )


def _parsed(annotation: ast.expr) -> ast.expr | None:
    if not isinstance(annotation, ast.Constant):
        return annotation
    try:
        return ast.parse(str(annotation.value).strip(), mode="eval").body
    except SyntaxError:
        return None


def _generator_name(
    source: SourceText,
    tree: ast.Module,
    *,
    local: str,
    context: tuple[ast.expr, Mapping[str, str]],
    edits: list[TextEdit],
) -> str | None:
    """The local name for AsyncGenerator; adds the import edits it needs.

    `context` holds the annotation being replaced and the stable imports. An
    existing AsyncGenerator import is reused. Otherwise the AsyncIterator alias
    is renamed when nothing else names it, or AsyncGenerator joins its import.
    """
    annotation, bindings = context
    module = bindings[local].rpartition(".")[0]
    existing = next(
        (
            name
            for name, target in bindings.items()
            if target == f"{module}.AsyncGenerator"
        ),
        None,
    )
    found = top_level_import(tree, local)
    unused = found is not None and not name_used(tree, local, ignore=[annotation])
    if existing is not None:
        if found is not None and unused and (removal := remove_import(source, *found)):
            edits.append(removal)
        return existing
    if found is None or name_used(tree, "AsyncGenerator"):
        return None
    statement, alias = found
    if unused and alias.asname is None and isinstance(statement, ast.ImportFrom):
        start = source.span(alias)[0]
        edits.append(
            TextEdit.at(source.text, (start, start + len(alias.name)), "AsyncGenerator")
        )
    else:
        edits.append(
            add_from_import(source, tree, module=module, name="AsyncGenerator")
        )
    return "AsyncGenerator"


def _replacement(node: ast.expr | None, bindings: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None
    if (
        isinstance(node, ast.Subscript)
        and not isinstance(node.slice, ast.Tuple)
        and qualified_name(node.value, bindings)
        in {"collections.abc.AsyncIterator", "typing.AsyncIterator"}
    ):
        return f"AsyncGenerator[{ast.unparse(node.slice)}, None]"
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
    for node in tree_nodes(tree):
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
