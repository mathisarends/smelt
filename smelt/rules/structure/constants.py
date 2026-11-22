from __future__ import annotations

import ast
import textwrap
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import tree_nodes
from smelt.diagnostics.fixes import (
    Applicability,
    Fix,
    FixAvailability,
    TextEdit,
    TextSpan,
)
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.edits import SourceText, name_used

if TYPE_CHECKING:
    from collections.abc import Iterator


class PrivateClassConstant(Rule):
    file_local = True
    code = "LAY008"
    name = "layout/misplaced-private-constant"
    category = Category.LAYOUT
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="A private literal constant is declared on an ordinary class instead of its module.",
        rationale="Implementation constants such as a controller's supported file extensions belong beside the class as private module constants when they do not form part of its object contract. This opt-in convention reports private UPPER_CASE names assigned literal scalars or recursively literal sets, lists, tuples and dictionaries in production class bodies, including annotated, chained and guarded assignments. It checks nested and local classes independently. Classes with bases, decorators or class keywords are excluded conservatively because inheritance, enums, data models and metaclasses can give attributes a class-specific contract. Public names, lowercase fields, dunder names, instance assignments, method locals, calls, comprehensions and values referring to other names are outside scope. This is a placement convention; syntax alone cannot prove that callers never override or mutate an attribute.",
        bad='class DownloadController:\n    _DOWNLOADABLE_EXTENSIONS = {".pdf", ".zip"}\n\n    def supports(self, extension: str) -> bool:\n        return extension in self._DOWNLOADABLE_EXTENSIONS',
        good='_DOWNLOADABLE_EXTENSIONS = {".pdf", ".zip"}\n\n\nclass DownloadController:\n    def supports(self, extension: str) -> bool:\n        return extension in _DOWNLOADABLE_EXTENSIONS',
        fix="Move the constant to the same module with a leading underscore, then update class-qualified and instance-qualified reads to use the constant name directly. Check name collisions, initialization order, name mangling, mutation, subclass overrides and external consumers before moving it. Keep intentional class contracts with a reasoned suppression or package override. Enable rules: {layout/misplaced-private-constant: error} to block the convention, or --select LAY008 to review it. check --fix --unsafe-fixes moves a single-target assignment of a top-level class above it and rewrites the reads when every read goes through the class or a method's receiver and the module does not otherwise use the name.",
        canonical=("Declare private literal constants at module level, not on a class"),
        config=("rules", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            text: SourceText | None = None
            for cls in tree_nodes(tree):
                if (
                    not isinstance(cls, ast.ClassDef)
                    or cls.bases
                    or cls.decorator_list
                    or cls.keywords
                ):
                    continue
                for statement in cls.body:
                    for target in _constant_targets(statement):
                        text = text or SourceText(ctx.files.read_text(source.path))
                        fix = _fix(text, tree, owner=cls, statement=statement)
                        yield self.violation(
                            f"move private constant {cls.name}.{target.id} to module scope",
                            path=source.path,
                            line=target.lineno,
                            column=target.col_offset + 1,
                            end_line=target.end_lineno,
                            end_column=(target.end_col_offset or 0) + 1,
                            source_module=source.module,
                            expected={
                                "class": cls.name,
                                "constant": target.id,
                                "placement": "module",
                                "visibility": "private",
                                "automatic_rewrite": fix is not None,
                            },
                            hint=f"Declare {target.id} at module scope and update its reads; review subclass overrides, mutation and consumers first.",
                            fix=fix,
                        )


def _constant_targets(node: ast.AST) -> Iterator[ast.Name]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return
    if isinstance(node, ast.Assign | ast.AnnAssign):
        if node.value is not None and _is_literal(node.value):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id.startswith("_")
                    and not target.id.endswith("__")
                    and target.id.isupper()
                ):
                    yield target
        return
    for child in ast.iter_child_nodes(node):
        yield from _constant_targets(child)


def _is_literal(node: ast.expr) -> bool:
    match node:
        case ast.Constant():
            return True
        case (
            ast.Set(elts=elements) | ast.List(elts=elements) | ast.Tuple(elts=elements)
        ):
            return all(_is_literal(element) for element in elements)
        case ast.Dict(keys=keys, values=values):
            return all(key is not None and _is_literal(key) for key in keys) and all(
                _is_literal(value) for value in values
            )
        case ast.UnaryOp(op=ast.UAdd() | ast.USub(), operand=ast.Constant(value=value)):
            return isinstance(value, int | float | complex) and not isinstance(
                value, bool
            )
        case _:
            return False


def _fix(
    source: SourceText,
    tree: ast.Module,
    *,
    owner: ast.ClassDef,
    statement: ast.stmt,
) -> Fix | None:
    """Move the constant above its class and read it by name.

    Unsafe: subclasses, instances and outside callers can no longer override or
    reach it through the class. Refused unless every read is qualified by the
    class or a method's receiver and the module name is otherwise unused.
    """
    match statement:
        case (
            ast.Assign(targets=[ast.Name(id=name)])
            | ast.AnnAssign(target=ast.Name(id=name), simple=1)
        ):
            pass
        case _:
            return None
    if (
        owner not in tree.body
        or len(owner.body) == 1
        or name.startswith("__")
        or not source.stands_alone(statement)
        or name_used(tree, name, ignore=[statement])
    ):
        return None
    reads = _qualified_reads(tree, owner, name)
    if reads is None:
        return None
    start, end = source.lines(statement)
    if not source.text[end : source.line_end_at(end)].strip():
        end = source.line_end_at(end)
    indentation = source.indentation(statement)
    declaration = textwrap.dedent(indentation + source.segment(statement))
    edits = [
        source.insert(source.block_start(owner), f"{declaration}\n\n\n"),
        TextEdit.at(source.text, TextSpan(start, end), ""),
    ]
    edits.extend(source.replace(read, name) for read in reads)
    return Fix(
        Applicability.UNSAFE,
        f"Move {name} to module scope as a private constant",
        tuple(edits),
    )


def _qualified_reads(
    tree: ast.Module, owner: ast.ClassDef, name: str
) -> list[ast.Attribute] | None:
    """Reads of ``Class.name`` anywhere and ``self.name`` in the class methods.

    None when any other access, or any write, could reach the attribute.
    """
    receivers: dict[int, str] = {}
    for method in owner.body:
        if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
            positional = [*method.args.posonlyargs, *method.args.args]
            if positional:
                for node in tree_nodes(method):
                    receivers[id(node)] = positional[0].arg
    reads: list[ast.Attribute] = []
    for node in tree_nodes(tree):
        if not isinstance(node, ast.Attribute) or node.attr != name:
            continue
        if not isinstance(node.ctx, ast.Load) or not isinstance(node.value, ast.Name):
            return None
        if node.value.id not in {owner.name, receivers.get(id(node))}:
            return None
        reads.append(node)
    return reads
