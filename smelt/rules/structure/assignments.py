from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.parsing import child_nodes
from smelt.diagnostics.fixes import Applicability, Fix, FixAvailability
from smelt.diagnostics.violation import Category, Violation
from smelt.rules.base import Rule, RuleDoc
from smelt.rules.edits import SourceText

if TYPE_CHECKING:
    from collections.abc import Iterator


def _assignments(node: ast.AST) -> Iterator[ast.Assign]:
    if isinstance(
        node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
    ):
        return
    if isinstance(node, ast.Assign):
        yield node
    for child in child_nodes(node):
        yield from _assignments(child)


def _attributes(target: ast.expr, receiver: str) -> Iterator[ast.Attribute]:
    match target:
        case ast.Attribute(value=ast.Name(id=name)) if name == receiver:
            yield target
        case ast.Tuple(elts=elements) | ast.List(elts=elements):
            for element in elements:
                yield from _attributes(element, receiver)
        case ast.Starred(value=value):
            yield from _attributes(value, receiver)


def _qualified(node: ast.expr, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified(node.value, bindings)
        return f"{parent}.{node.attr}" if parent else None
    return None


class BundledInstanceAssignment(Rule):
    file_local = True
    code = "IDM001"
    name = "idioms/bundled-instance-assignment"
    category = Category.IDIOMS
    requires = frozenset({Index.SYNTAX})
    fix_availability = FixAvailability.SOMETIMES
    doc = RuleDoc(
        summary="An assignment writes multiple direct instance attributes under a separate-assignment convention.",
        rationale="Separate field assignments make object initialization easy to scan and extend. This opt-in package convention reports tuple/list unpacking and chained assignments that write multiple direct attributes of a method's first positional parameter, regardless of its name. It leaves local unpacking and single-field assignments alone. Recognized static/class methods and nested helper bodies are excluded; nested classes are inspected with their own receiver. This is a readability policy, not a logic error.",
        bad="class Inbox:\n    def __init__(self, log, control):\n        self.log, self.control = log, control",
        good="class Inbox:\n    def __init__(self, log, control):\n        self.log = log\n        self.control = control",
        fix="Use one direct instance attribute write per statement. Preserve evaluation timing by unpacking into locals before assigning fields when appropriate. check --fix --unsafe-fixes can split chained writes of a name or constant and same-length tuple or list unpacking into direct fields. Review the change: splitting can repeat a name lookup or write a field before a later value is evaluated.",
        canonical="Assign one instance attribute per statement",
        config=("rules", "style.overrides"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            syntax = ctx.syntax.for_path(source.path)
            if syntax is None:
                continue
            text: SourceText | None = None
            for cls in syntax.nodes:
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
                            text = text or SourceText(ctx.files.read_text(source.path))
                            fix = _fix(text, assignment, args[0].arg)
                            yield self.violation(
                                f"assignment writes {len(targets)} instance attributes; use separate statements",
                                path=source.path,
                                line=assignment.lineno,
                                column=assignment.col_offset + 1,
                                end_line=assignment.end_lineno,
                                end_column=(assignment.end_col_offset or 0) + 1,
                                source_module=source.module,
                                expected={
                                    "class": cls.name,
                                    "method": method.name,
                                    "attributes": [
                                        ast.unparse(target) for target in targets
                                    ],
                                    "automatic_split": fix is not None,
                                },
                                hint="Write each field separately, preserving the original evaluation order and unpacking behavior.",
                                fix=fix,
                            )


def _fix(source: SourceText, assignment: ast.Assign, receiver: str) -> Fix | None:
    """Propose one assignment per field for simple values and direct targets.

    Splitting can repeat a lookup in a chain or start writing before all the
    unpacked values have been evaluated.
    """
    pairs = _pairs(assignment, receiver)
    if pairs is None or not source.stands_alone(assignment):
        return None
    if "#" in source.segment(assignment):
        return None
    indentation = source.indentation(assignment)
    lines = [
        f"{source.segment(target)} = {source.segment(value)}" for target, value in pairs
    ]
    return Fix(
        Applicability.UNSAFE,
        "Assign each instance attribute in its own statement",
        (source.replace(assignment, f"\n{indentation}".join(lines)),),
    )


def _pairs(
    assignment: ast.Assign, receiver: str
) -> list[tuple[ast.expr, ast.expr]] | None:
    """The (field, value) writes in order for supported assignment shapes."""
    value = assignment.value
    if len(assignment.targets) > 1:
        if not _simple(value) or not all(
            _field(target, receiver) for target in assignment.targets
        ):
            return None
        return [(target, value) for target in assignment.targets]
    [target] = assignment.targets
    if not (
        isinstance(target, ast.Tuple | ast.List)
        and isinstance(value, ast.Tuple | ast.List)
        and len(target.elts) == len(value.elts)
        and all(_field(element, receiver) for element in target.elts)
        and all(_simple(element) for element in value.elts)
    ):
        return None
    return list(zip(target.elts, value.elts, strict=True))


def _field(target: ast.expr, receiver: str) -> bool:
    match target:
        case ast.Attribute(value=ast.Name(id=name)):
            return name == receiver
        case _:
            return False


def _simple(value: ast.expr) -> bool:
    return isinstance(value, ast.Name | ast.Constant)
