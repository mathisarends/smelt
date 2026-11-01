from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.navigation import NavigationNamespaces
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class DeepObjectNavigation(Rule):
    code = "SMT322"
    name = "deep-object-navigation"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Review a method call reached through multiple instance attributes.",
        rationale=(
            "An instance method that reaches through several stored collaborators depends on their internal arrangement. "
            "This opt-in Law of Demeter review reports calls with at least two consecutive attribute accesses from the "
            "instance receiver before the first method call, such as self.browser.input.motor(). One finding covers "
            "a whole receiver call chain. Direct collaborator calls, pure data reads and fluent chains starting from "
            "a direct collaborator are excluded. Module objects stored from unshadowed imports or declared as ModuleType, "
            "explicit SimpleNamespace trees, and typed dataclass/Pydantic record paths ending in known containers or "
            "values are namespaces rather than collaborator graphs. Known pathlib parent paths and subprocess pipes "
            "also retain their standard APIs. Namespace evidence comes from annotations, direct constructor assignments "
            "and property return annotations; analyzed imports and re-exports are followed without executing code. "
            "Every intermediate object must be a known namespace or value: a record containing a service does not "
            "exempt navigation into that service. Names such as api, client, get or append alone exempt nothing. "
            "Unknown SDK types and dynamic property/data flow remain review limitations. Decorated and implicit "
            "class/static methods, nested helper bodies, comprehensions, receiver rebinding and navigation through "
            "parameters, aliases or subscripts are outside the review. This is a bounded review, not proof of a design defect."
        ),
        bad="class Controller:\n    def aim(self, width, height):\n        return self.browser.input.motor().aim(width, height)",
        good="class Controller:\n    def __init__(self, motor):\n        self.motor = motor\n\n    def aim(self, width, height):\n        return self.motor.aim(width, height)",
        fix="Review whether the caller should receive the needed collaborator directly or ask its direct collaborator to perform the operation. A local variable alone does not reduce structural coupling. Preserve factory/property evaluation and object lifetimes; repeated lookups need not return the same object. Keep intentional API navigation with a package rules override set to off or a reasoned SMT322 suppression. No automatic rewrite is performed. Enable with rules: {deep-object-navigation: hint}, globally or in a package contract, or --select SMT322.",
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        namespaces = NavigationNamespaces(ctx.syntax, ctx.files)
        for source in ctx.files.sources.values():
            for cls, method, receiver in _instance_methods(ctx.asts.parse(source.path)):
                for statement in method.body:
                    for call, attributes in _calls(statement, receiver):
                        if namespaces.contains(source.module, cls, attributes):
                            continue
                        path = ".".join((receiver, *attributes))
                        yield self.violation(
                            f"review method call through {path}; it traverses {len(attributes)} instance attributes",
                            path=source.path,
                            line=call.lineno,
                            column=call.col_offset + 1,
                            end_line=call.end_lineno,
                            end_column=(call.end_col_offset or 0) + 1,
                            source_module=source.module,
                            expected={
                                "class": cls.name,
                                "method": method.name,
                                "callable": ast.unparse(call.func),
                                "stored_path": path,
                                "stored_depth": len(attributes),
                                "automatic_rewrite": False,
                            },
                            hint="Consider injecting the needed collaborator or delegating the operation to a direct collaborator; review intentional API navigation.",
                        )


def _instance_methods(
    tree: ast.Module,
) -> Iterator[tuple[ast.ClassDef, ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for method in cls.body:
            if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            args = [*method.args.posonlyargs, *method.args.args]
            if (
                method.decorator_list
                or method.name in {"__new__", "__init_subclass__", "__class_getitem__"}
                or not args
            ):
                continue
            receiver = args[0].arg
            if any(
                _rebinds(node, receiver)
                for statement in method.body
                for node in ast.walk(statement)
            ):
                continue
            yield cls, method, receiver


def _rebinds(node: ast.AST, receiver: str) -> bool:
    match node:
        case (
            ast.FunctionDef(name=name)
            | ast.AsyncFunctionDef(name=name)
            | ast.ClassDef(name=name)
            | ast.Name(id=name, ctx=ast.Store() | ast.Del())
            | ast.ExceptHandler(name=name)
            | ast.MatchAs(name=name)
            | ast.MatchStar(name=name)
            | ast.MatchMapping(rest=name)
        ):
            return name == receiver
        case ast.Import(names=names) | ast.ImportFrom(names=names):
            return any(
                (alias.asname or alias.name.split(".")[0]) == receiver
                for alias in names
            )
    return False


def _stored_attributes(call: ast.Call, receiver: str) -> tuple[str, ...]:
    innermost = call
    current: ast.expr = call
    while isinstance(current, ast.Call | ast.Attribute):
        if isinstance(current, ast.Call):
            innermost = current
            current = current.func
        else:
            current = current.value
    if not isinstance(current, ast.Name) or current.id != receiver:
        return ()
    if not isinstance(innermost.func, ast.Attribute):
        return ()
    attributes: list[str] = []
    current = innermost.func.value
    while isinstance(current, ast.Attribute):
        attributes.append(current.attr)
        current = current.value
    return tuple(reversed(attributes)) if len(attributes) >= 2 else ()  # noqa: PLR2004


def _calls(
    node: ast.AST, receiver: str, *, in_receiver: bool = False
) -> Iterator[tuple[ast.Call, tuple[str, ...]]]:
    if isinstance(
        node,
        ast.FunctionDef
        | ast.AsyncFunctionDef
        | ast.ClassDef
        | ast.Lambda
        | ast.ListComp
        | ast.SetComp
        | ast.DictComp
        | ast.GeneratorExp,
    ):
        return
    if isinstance(node, ast.Call):
        attributes = _stored_attributes(node, receiver)
        if attributes and not in_receiver:
            yield node, attributes
        yield from _calls(
            node.func, receiver, in_receiver=in_receiver or bool(attributes)
        )
        for argument in (*node.args, *(keyword.value for keyword in node.keywords)):
            yield from _calls(argument, receiver)
    elif isinstance(node, ast.Attribute):
        yield from _calls(node.value, receiver, in_receiver=in_receiver)
    else:
        for child in ast.iter_child_nodes(node):
            yield from _calls(child, receiver)
