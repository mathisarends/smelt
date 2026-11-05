from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class PrivateClassConstant(Rule):
    file_local = True
    code = "SMT334"
    name = "private-class-constant"
    category = Category.STRUCTURE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A private literal constant is declared on an ordinary class instead of its module.",
        rationale="Implementation constants such as a controller's supported file extensions belong beside the class as private module constants when they do not form part of its object contract. This opt-in convention reports private UPPER_CASE names assigned literal scalars or recursively literal sets, lists, tuples and dictionaries in production class bodies, including annotated, chained and guarded assignments. It checks nested and local classes independently. Classes with bases, decorators or class keywords are excluded conservatively because inheritance, enums, data models and metaclasses can give attributes a class-specific contract. Public names, lowercase fields, dunder names, instance assignments, method locals, calls, comprehensions and values referring to other names are outside scope. This is a placement convention; syntax alone cannot prove that callers never override or mutate an attribute.",
        bad='class DownloadController:\n    _DOWNLOADABLE_EXTENSIONS = {".pdf", ".zip"}\n\n    def supports(self, extension: str) -> bool:\n        return extension in self._DOWNLOADABLE_EXTENSIONS',
        good='_DOWNLOADABLE_EXTENSIONS = {".pdf", ".zip"}\n\n\nclass DownloadController:\n    def supports(self, extension: str) -> bool:\n        return extension in _DOWNLOADABLE_EXTENSIONS',
        fix="Move the constant to the same module with a leading underscore, then update class-qualified and instance-qualified reads to use the constant name directly. Check name collisions, initialization order, name mangling, mutation, subclass overrides and external consumers before moving it. Keep intentional class contracts with a reasoned suppression or package override. Enable rules: {private-class-constant: error} to block the convention, or --select SMT334 to review it. No automatic move or rewrite is performed.",
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            for cls in ast.walk(ctx.asts.parse(source.path)):
                if (
                    not isinstance(cls, ast.ClassDef)
                    or cls.bases
                    or cls.decorator_list
                    or cls.keywords
                ):
                    continue
                for statement in cls.body:
                    for target in _constant_targets(statement):
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
                                "automatic_rewrite": False,
                            },
                            hint=f"Declare {target.id} at module scope and update its reads; review subclass overrides, mutation and consumers first.",
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
