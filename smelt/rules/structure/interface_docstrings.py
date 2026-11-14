from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator

_ABSTRACT_DECORATORS = frozenset(
    {
        "abstractmethod",
        "abstractproperty",
        "abstractclassmethod",
        "abstractstaticmethod",
    }
)
_EXEMPT_DECORATORS = frozenset({"override", "overload", "setter", "deleter"})
_NOT_IMPLEMENTED = "NotImplementedError"


class MissingInterfaceDocstring(Rule):
    file_local = True
    code = "SMT348"
    name = "missing-interface-docstring"
    category = Category.STRUCTURE
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="An abstract or Protocol method has a placeholder body and no docstring.",
        rationale=(
            "A port or interface method has no implementation to read, so its "
            "docstring is the only place that states the contract: what the "
            "method guarantees, what it requires and what it leaves untouched. "
            "A bare ... body leaves implementers and callers to guess, and one "
            "undocumented member next to documented siblings usually means the "
            "contract was forgotten, not that it is obvious. This opt-in review "
            "reports @abstractmethod methods (including abstract properties) and "
            "methods of typing.Protocol classes whose body is only ..., pass or "
            "raise NotImplementedError and which have no docstring. Methods with "
            "a default implementation, @overload/@override signatures, property "
            "setters/deleters, dunder methods other than __call__ and tests are "
            "not reported. Ordinary Protocol attributes have no body and are out "
            "of scope."
        ),
        bad=(
            "class BrowserAgentProfile(ABC):\n"
            "    @property\n"
            "    @abstractmethod\n"
            "    def initial_tab_id(self) -> UUID: ...\n\n"
            "    @abstractmethod\n"
            "    async def revoke(self) -> None: ..."
        ),
        good=(
            "class BrowserAgentProfile(ABC):\n"
            "    @property\n"
            "    @abstractmethod\n"
            "    def initial_tab_id(self) -> UUID:\n"
            '        """The tab retained when the profile grant was acquired."""\n\n'
            "    @abstractmethod\n"
            "    async def revoke(self) -> None:\n"
            '        """Fence native access and settle all resource leases."""'
        ),
        fix=(
            "Replace the placeholder body with a docstring that states the "
            "method's contract; a docstring alone is a valid body, so drop the "
            "... (Ruff PIE790 reports a placeholder left after a docstring). "
            "No automatic rewrite is performed. Enable rules: "
            "{missing-interface-docstring: hint}, or select SMT348. Package "
            "overrides and reasoned inline suppressions are supported."
        ),
        config=("rules",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for source in ctx.files.sources.values():
            tree = ctx.asts.parse(source.path)
            for found in _undocumented_members(tree):
                qualified = f"{found.owner.name}.{found.method.name}"
                yield self.violation(
                    f"{found.kind.value.capitalize()} method {qualified} has a "
                    "placeholder body and no docstring",
                    path=source.path,
                    line=found.method.lineno,
                    column=found.method.col_offset + 1,
                    source_module=source.module,
                    expected={
                        "class": found.owner.name,
                        "method": found.method.name,
                        "interface": found.kind.value,
                        "automatic_rewrite": False,
                    },
                    hint=(
                        f"Replace the body of {qualified} with a docstring that "
                        "states its contract."
                    ),
                )


class _InterfaceKind(StrEnum):
    ABSTRACT = "abstract"
    PROTOCOL = "protocol"


@dataclass(frozen=True)
class _Member:
    owner: ast.ClassDef
    method: ast.FunctionDef | ast.AsyncFunctionDef
    kind: _InterfaceKind


def _undocumented_members(tree: ast.Module) -> Iterator[_Member]:
    for owner in ast.walk(tree):
        if not isinstance(owner, ast.ClassDef):
            continue
        protocol = any(_is_protocol(base) for base in owner.bases)
        for method in owner.body:
            if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            decorators = {_decorator_name(item) for item in method.decorator_list}
            if decorators & _ABSTRACT_DECORATORS:
                kind = _InterfaceKind.ABSTRACT
            elif protocol:
                kind = _InterfaceKind.PROTOCOL
            else:
                continue
            if (
                not decorators & _EXEMPT_DECORATORS
                and not _is_dunder(method.name)
                and ast.get_docstring(method) is None
                and all(_is_placeholder(statement) for statement in method.body)
            ):
                yield _Member(owner, method, kind)


def _is_protocol(base: ast.expr) -> bool:
    if isinstance(base, ast.Subscript):
        base = base.value
    return _dotted(base).rpartition(".")[2] == "Protocol"


def _decorator_name(decorator: ast.expr) -> str:
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    return _dotted(decorator).rpartition(".")[2]


def _is_dunder(name: str) -> bool:
    return name != "__call__" and name.startswith("__") and name.endswith("__")


def _is_placeholder(statement: ast.stmt) -> bool:
    match statement:
        case ast.Pass():
            return True
        case ast.Expr(value=ast.Constant(value=value)):
            return value is Ellipsis
        case ast.Raise(exc=ast.Name(id=name) | ast.Call(func=ast.Name(id=name))):
            return name == _NOT_IMPLEMENTED
    return False


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (owner := _dotted(node.value)):
        return f"{owner}.{node.attr}"
    return ""
