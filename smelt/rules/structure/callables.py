from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class PreferCallableInstance(Rule):
    code = "SMT333"
    name = "prefer-callable-instance"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    file_local = True
    doc = RuleDoc(
        summary="Review __call__ and an operation-based class name for a single public method.",
        rationale=(
            "A class with one public instance method can express its operation through __call__ "
            "and a class name describing that operation. This opt-in design review reports "
            "one candidate per plain class with exactly one directly declared public method. "
            "Private helpers and __init__ do not count as public operations. An existing __call__ "
            "is already a callable API and is never reported. Constructor and operation signatures "
            "are reported separately, including positional-only, keyword-only and variadic arguments: "
            "construction configures the object, while __call__ must retain the method's call-time arguments "
            "and async behavior. Classes with bases, metaclass keywords, class or method decorators, "
            "additional special methods, class-level data or conditional definitions are excluded. "
            "This keeps inherited APIs, descriptors, dataclasses and explicit protocols outside this "
            "bounded syntax review. Test files are excluded. A single method does not prove a design "
            "defect: published named APIs and stateful lifecycles can justify keeping the class as it is. "
            "Dynamic monkey-patching and external API consumers are not resolved."
        ),
        bad=(
            "class ReportService:\n"
            "    def __init__(self, storage):\n"
            "        self._storage = storage\n\n"
            "    def generate(self, report_id, *, refresh=False):\n"
            "        return self._storage.load(report_id, refresh=refresh)"
        ),
        good=(
            "class GenerateReport:\n"
            "    def __init__(self, storage):\n"
            "        self._storage = storage\n\n"
            "    def __call__(self, report_id, *, refresh=False):\n"
            "        return self._storage.load(report_id, refresh=refresh)"
        ),
        fix=(
            "Consider renaming the sole public method to __call__ and naming the class after its "
            "operation (for example, ReportService.generate becomes GenerateReport.__call__). "
            "Preserve the constructor signature, call-time parameters, defaults, return annotations "
            "and async behavior; do not move constructor dependencies into __call__. Review callers, "
            "dependency injection registrations and public compatibility before changing either name. "
            "No names, arguments or callers are rewritten automatically. Enable rules: "
            "{prefer-callable-instance: hint}, globally or in a package contract, or --select SMT333. "
            "Keep an intentional named API with a package rules override set to off or a reasoned SMT333 suppression."
        ),
        config=("rules", "structure.contracts"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            for cls in ast.walk(ctx.asts.parse(source.path)):
                if not isinstance(cls, ast.ClassDef):
                    continue
                methods = _plain_methods(cls)
                public = [
                    method for method in methods if not method.name.startswith("_")
                ]
                if len(public) != 1 or any(m.name == "__call__" for m in methods):
                    continue
                [method] = public
                constructor = next((m for m in methods if m.name == "__init__"), None)
                yield self.violation(
                    f"review {cls.name}.{method.name} as __call__ with an operation-based class name",
                    path=source.path,
                    line=cls.lineno,
                    column=cls.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "class": cls.name,
                        "method": method.name,
                        "candidate_method": "__call__",
                        "constructor_signature": _signature(constructor)
                        if constructor
                        else "",
                        "operation_signature": _signature(method),
                        "async_operation": isinstance(method, ast.AsyncFunctionDef),
                        "review_class_name": True,
                        "review_required": True,
                        "automatic_rewrite": False,
                    },
                    hint="Consider __call__ and a class name describing the operation. Preserve constructor and call-time arguments separately; review all callers and public compatibility before renaming.",
                )


def _plain_methods(cls: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    if cls.bases or cls.keywords or cls.decorator_list:
        return []
    methods: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    names: set[str] = set()
    for node in cls.body:
        if isinstance(node, ast.Pass) or (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return []
        if (
            node.decorator_list
            or not (node.args.posonlyargs or node.args.args)
            or node.name in names
            or (
                node.name.startswith("__")
                and node.name.endswith("__")
                and node.name not in {"__init__", "__call__"}
            )
        ):
            return []
        names.add(node.name)
        methods.append(node)
    return methods


def _signature(method: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = method.args
    positional = [*args.posonlyargs, *args.args][1:]
    return ast.unparse(
        ast.arguments(
            posonlyargs=args.posonlyargs[1:],
            args=args.args if args.posonlyargs else args.args[1:],
            vararg=args.vararg,
            kwonlyargs=args.kwonlyargs,
            kw_defaults=args.kw_defaults,
            kwarg=args.kwarg,
            defaults=args.defaults[-len(positional) :] if positional else [],
        )
    )
