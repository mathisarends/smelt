from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class PreferDeferredAnnotations(Rule):
    file_local = True
    code = "SMT346"
    name = "prefer-deferred-annotations"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="An annotation is quoted where deferred evaluation makes quotes unnecessary.",
        rationale=(
            'Quoted annotations such as list["BrowserWorkspaceTab"] are invisible '
            "to rename refactorings, read differently from their neighbors and "
            "need a decision at every forward reference. One module-level "
            "from __future__ import annotations, or a declared Python 3.14+ "
            "minimum, defers every annotation instead. This opt-in style review "
            "reports one finding per module that quotes annotations of parameters, "
            "returns or annotated assignments: on 3.14+ targets or with the "
            "future import, the quotes are redundant; otherwise the module should "
            "add the future import. Strings inside Literal and Annotated metadata "
            "are values, not annotations, and are ignored, as are quoted names in "
            "runtime expressions such as cast() or TypeVar(bound=...). A package "
            "contract with future_annotations: forbid keeps quotes on targets "
            "before 3.14. Tests are not checked."
        ),
        bad=(
            "class BrowserHandoff(ABC):\n"
            "    @abstractmethod\n"
            '    async def tabs(self) -> list["BrowserWorkspaceTab"]: ...'
        ),
        good=(
            "from __future__ import annotations\n\n"
            "class BrowserHandoff(ABC):\n"
            "    @abstractmethod\n"
            "    async def tabs(self) -> list[BrowserWorkspaceTab]: ..."
        ),
        fix=(
            "Remove the quotes; on targets before Python 3.14 add "
            "from __future__ import annotations first. The future import makes "
            "every annotation in the module a string at runtime, so verify "
            "runtime annotation consumers (dataclasses, Pydantic, FastAPI, "
            "dependency injection) and names imported only under TYPE_CHECKING. "
            "No automatic rewrite is performed. Enable rules: "
            "{prefer-deferred-annotations: hint}, or select SMT346."
        ),
        config=("rules", "structure.contracts", "project.python_version"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            quoted = sorted(
                (
                    node
                    for annotation in _annotations(tree)
                    for node in _quoted(annotation)
                ),
                key=lambda node: (node.lineno, node.col_offset),
            )
            if not quoted:
                continue
            target = ctx.python_targets.for_path(source.absolute)
            contract = ctx.contracts.policy_for(source.module, "future_annotations")
            policy = contract.spec.future_annotations if contract else None
            if target.deferred_annotations:
                action, reason = "unquote", "annotations are deferred on Python 3.14+"
            elif _has_future_import(tree):
                action, reason = (
                    "unquote",
                    "the annotations future import already defers them",
                )
            elif policy == "forbid":
                continue
            else:
                action, reason = (
                    "add-future-import",
                    "an annotations future import defers them",
                )
            first = quoted[0]
            yield self.violation(
                f"{len(quoted)} quoted annotation(s); {reason}",
                path=source.path,
                line=first.lineno,
                column=first.col_offset + 1,
                source_module=source.module,
                expected={
                    "action": action,
                    "annotations": [
                        {"line": node.lineno, "text": node.value} for node in quoted
                    ],
                    "future_annotations": policy,
                    "python": target.to_json(),
                    "automatic_rewrite": False,
                },
                hint=(
                    "Remove the quotes."
                    if action == "unquote"
                    else "Add from __future__ import annotations and remove the "
                    "quotes; verify runtime annotation consumers."
                ),
            )


def _annotations(tree: ast.Module) -> Iterator[ast.expr]:
    for node in ast.walk(tree):
        match node:
            case ast.arg(annotation=ast.expr() as annotation):
                yield annotation
            case (
                ast.FunctionDef(returns=ast.expr() as returns)
                | ast.AsyncFunctionDef(returns=ast.expr() as returns)
            ):
                yield returns
            case ast.AnnAssign():
                yield node.annotation


def _quoted(node: ast.expr) -> Iterator[ast.Constant]:
    match node:
        case ast.Constant(value=str()):
            yield node
        case ast.Subscript(value=value, slice=index):
            name = _last_name(value)
            if name == "Literal":
                return
            if name == "Annotated" and isinstance(index, ast.Tuple) and index.elts:
                yield from _quoted(index.elts[0])
                return
            yield from _quoted(value)
            yield from _quoted(index)
        case _:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    yield from _quoted(child)


def _last_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _has_future_import(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and node.level == 0
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )
