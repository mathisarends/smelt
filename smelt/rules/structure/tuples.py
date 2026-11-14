from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

_MIN_ELEMENTS = 3
_TUPLES = frozenset({"tuple", "Tuple", "typing.Tuple", "typing_extensions.Tuple"})
_EXEMPT_DECORATORS = frozenset({"override", "overload"})


class AnonymousTupleType(Rule):
    file_local = True
    code = "SMT347"
    name = "anonymous-tuple-type"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A fixed-length tuple type leaves its fields unnamed.",
        rationale=(
            "dict[UUID, tuple[UUID, UUID, BrowserConnection]] does not say which "
            "UUID is which; readers decode entry[0] and entry[1] from call sites, "
            "and swapping them still type-checks. This opt-in review reports "
            "fixed-length tuple types in annotations and type aliases with three "
            "or more elements, or with an element type that repeats. Variadic "
            "tuple[X, ...], empty tuples, two different element types, quoted "
            "annotations, dunder/@override/@overload signatures (dictated "
            "elsewhere) and tests are not reported."
        ),
        bad=(
            "class Connections:\n"
            "    def __init__(self) -> None:\n"
            "        self._by_tab: dict[UUID, tuple[UUID, UUID, Connection]] = {}"
        ),
        good=(
            "class Route(NamedTuple):\n"
            "    user_id: UUID\n"
            "    run_id: UUID\n"
            "    connection: Connection\n\n"
            "class Connections:\n"
            "    def __init__(self) -> None:\n"
            "        self._by_tab: dict[UUID, Route] = {}"
        ),
        fix=(
            "Introduce a NamedTuple (keeps unpacking and indexing) or a frozen "
            "dataclass that names each field, and use it in place of the tuple. "
            "Keep a tuple whose shape an external API dictates with a reasoned "
            "suppression. No automatic rewrite is performed. Enable rules: "
            "{anonymous-tuple-type: hint}, or select SMT347."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            for found in _anonymous_tuples(tree):
                spelled = ast.unparse(found.node)
                reason = (
                    "repeated element type"
                    if found.repeated
                    else "three or more elements"
                )
                yield self.violation(
                    f"{spelled} leaves its fields unnamed ({reason})",
                    path=source.path,
                    line=found.node.lineno,
                    column=found.node.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "tuple": spelled,
                        "elements": found.elements,
                        "repeated": found.repeated,
                        "automatic_rewrite": False,
                    },
                    hint="Name the fields with a NamedTuple or frozen dataclass.",
                )


@dataclass(frozen=True)
class _Tuple:
    node: ast.Subscript
    elements: list[str]
    repeated: list[str]


def _anonymous_tuples(tree: ast.Module) -> Iterator[_Tuple]:
    for annotation in _annotations(tree):
        for node in ast.walk(annotation):
            if (
                isinstance(node, ast.Subscript)
                and _dotted(node.value) in _TUPLES
                and isinstance(node.slice, ast.Tuple)
                and not any(
                    isinstance(element, ast.Constant) and element.value is Ellipsis
                    for element in node.slice.elts
                )
            ):
                elements = [ast.unparse(element) for element in node.slice.elts]
                repeated = sorted(
                    {element for element in elements if elements.count(element) > 1}
                )
                if repeated or len(elements) >= _MIN_ELEMENTS:
                    yield _Tuple(node, elements, repeated)


def _annotations(tree: ast.Module) -> Iterator[ast.expr]:
    for node in ast.walk(tree):
        match node:
            case ast.FunctionDef() | ast.AsyncFunctionDef() if not _dictated(node):
                arguments = node.args
                for argument in (
                    *arguments.posonlyargs,
                    *arguments.args,
                    *arguments.kwonlyargs,
                    *(a for a in (arguments.vararg, arguments.kwarg) if a),
                ):
                    if argument.annotation is not None:
                        yield argument.annotation
                if node.returns is not None:
                    yield node.returns
            case ast.AnnAssign():
                yield node.annotation
                if (
                    _dotted(node.annotation) in {"TypeAlias", "typing.TypeAlias"}
                    and node.value
                ):
                    yield node.value
            case ast.TypeAlias():
                yield node.value


def _dictated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if node.name.startswith("__") and node.name.endswith("__"):
        return True
    return any(
        _dotted(decorator).rpartition(".")[2] in _EXEMPT_DECORATORS
        for decorator in node.decorator_list
        if _dotted(decorator)
    )


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (owner := _dotted(node.value)):
        return f"{owner}.{node.attr}"
    return ""
