import ast
import re
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import ModuleKind
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.code.common import node_violation
from smelt.rules.common import join

if TYPE_CHECKING:
    from collections.abc import Iterator

_RESOLVE_METHODS = frozenset({"get", "aget", "get_sync", "resolve", "aresolve"})
_CONTAINER_NAME = re.compile(r"(container|injector|resolver)$", re.IGNORECASE)


def _receiver_name(node: ast.expr) -> str | None:
    match node:
        case ast.Name(id=name):
            return name
        case ast.Attribute(attr=attr):
            return attr
        case ast.Call(func=func):
            return _receiver_name(func)
        case _:
            return None


class ContainerUsage(BaseRule):
    code = "SMT205"
    name = "container-usage"
    category = Category.CODE
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A DI container resolves a dependency outside the composition root.",
        rationale=(
            "Pulling dependencies out of a container (service locator) hides them from "
            "signatures, so neither readers nor tests can see what a class needs."
        ),
        bad=(
            "def start(container: Container) -> None:\n"
            "    sessions = container.get(VoiceSessionRepository)"
        ),
        good="def start(sessions: VoiceSessionRepository) -> None: ...",
        fix="Take the dependency as a parameter and resolve it in the composition root.",
        config=("architecture.composition_root", "architecture.di_frameworks"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        roots = ctx.config.architecture.composition_root
        if not roots:
            return
        model = ctx.model
        for syntax in ctx.syntax.sources():
            info = model.info(syntax.module)
            if info is None or info.kind is ModuleKind.COMPOSITION_ROOT:
                continue
            for node in syntax.walk():
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _RESOLVE_METHODS
                    and node.args
                ):
                    continue
                receiver = _receiver_name(node.func.value)
                if receiver is None or not _CONTAINER_NAME.search(receiver):
                    continue
                yield node_violation(
                    self,
                    ctx,
                    syntax,
                    node,
                    f"{receiver}.{node.func.attr}(...) resolves a dependency outside "
                    f"the composition root ({join(roots)})",
                    expected={"composition_root": list(roots)},
                    hint=(
                        "Accept the dependency as a constructor or function parameter "
                        f"and resolve it in {roots[0]}."
                    ),
                )
