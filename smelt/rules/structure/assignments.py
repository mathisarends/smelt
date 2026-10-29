from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Violation
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


def _assignments(node: ast.AST) -> Iterator[ast.Assign]:
    if isinstance(
        node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
    ):
        return
    if isinstance(node, ast.Assign):
        yield node
    for child in ast.iter_child_nodes(node):
        yield from _assignments(child)


def _attributes(target: ast.expr, receiver: str) -> Iterator[ast.Attribute]:
    if isinstance(target, ast.Attribute):
        if isinstance(target.value, ast.Name) and target.value.id == receiver:
            yield target
    elif isinstance(target, ast.Tuple | ast.List):
        for element in target.elts:
            yield from _attributes(element, receiver)
    elif isinstance(target, ast.Starred):
        yield from _attributes(target.value, receiver)


def _qualified(node: ast.expr, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified(node.value, bindings)
        return f"{parent}.{node.attr}" if parent else None
    return None


class BundledInstanceAssignment(BaseRule):
    code = "SMT314"
    name = "bundled-instance-assignment"
    category = Category.STRUCTURE
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="An assignment writes multiple direct instance attributes under a separate-assignment convention.",
        rationale="Separate field assignments make object initialization easy to scan and extend. This opt-in package convention reports tuple/list unpacking and chained assignments that write multiple direct attributes of a method's first positional parameter, regardless of its name. It leaves local unpacking and single-field assignments alone. Recognized static/class methods and nested helper bodies are excluded; nested classes are inspected with their own receiver. This is a readability policy, not a logic error.",
        bad="class Inbox:\n    def __init__(self, log, control):\n        self.log, self.control = log, control",
        good="class Inbox:\n    def __init__(self, log, control):\n        self.log = log\n        self.control = control",
        fix="Use one direct instance attribute write per statement. Preserve evaluation timing: unpack computed values into locals before assigning fields when appropriate. Parallel assignments, swaps, descriptors and unpacking failures can behave differently when split, so there is no automatic rewrite.",
        config=("structure.contracts",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            contract = ctx.contracts.policy_for(source.module, "instance_assignments")
            if contract is None or contract.spec.instance_assignments != "separate":
                continue
            syntax = ctx.syntax.for_path(source.path)
            if syntax is None:
                continue
            for cls in ast.walk(syntax.tree):
                if not isinstance(cls, ast.ClassDef):
                    continue
                for method in cls.body:
                    if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
                        continue
                    args = [*method.args.posonlyargs, *method.args.args]
                    if (
                        method.name
                        in {"__new__", "__init_subclass__", "__class_getitem__"}
                        or not args
                        or any(
                            _qualified(decorator, syntax.bindings)
                            in {
                                "staticmethod",
                                "classmethod",
                                "builtins.staticmethod",
                                "builtins.classmethod",
                            }
                            for decorator in method.decorator_list
                        )
                    ):
                        continue
                    for statement in method.body:
                        for assignment in _assignments(statement):
                            targets = [
                                target
                                for root in assignment.targets
                                for target in _attributes(root, args[0].arg)
                            ]
                            if len(targets) <= 1:
                                continue
                            yield self.violation(
                                f"assignment writes {len(targets)} instance attributes; use separate statements",
                                path=source.path,
                                line=assignment.lineno,
                                column=assignment.col_offset + 1,
                                end_line=assignment.end_lineno,
                                end_column=(assignment.end_col_offset or 0) + 1,
                                source_module=source.module,
                                expected={
                                    "contract": contract.name,
                                    "instance_assignments": "separate",
                                    "class": cls.name,
                                    "method": method.name,
                                    "attributes": [
                                        ast.unparse(target) for target in targets
                                    ],
                                    "automatic_split": False,
                                },
                                hint="Write each field separately, preserving the original evaluation order and unpacking behavior.",
                            )
