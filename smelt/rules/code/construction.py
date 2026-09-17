import ast
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import ModuleKind
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.code.common import (
    annotation_names,
    implementation_classes,
    implemented_ports,
    node_violation,
    scopes,
)
from smelt.rules.common import join, last_segment

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.syntax import ClassInfo


class ConcreteConstruction(BaseRule):
    code = "SMT201"
    name = "concrete-construction"
    category = Category.CODE
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX, Index.ROLES})
    doc = RuleDoc(
        summary=(
            "An adapter is instantiated outside its own layer or the composition root."
        ),
        rationale=(
            "Code that constructs its collaborators decides which implementation runs. "
            "That choice belongs in the composition root, so the rest of the code can "
            "depend on ports and stay testable with fakes."
        ),
        bad=(
            "# voice/application/session.py\n"
            "class StartSession:\n"
            "    def __init__(self) -> None:\n"
            "        self.sessions = SqlVoiceSessionRepository()"
        ),
        good=(
            "# voice/application/session.py\n"
            "class StartSession:\n"
            "    def __init__(self, sessions: VoiceSessionRepository) -> None:\n"
            "        self.sessions = sessions"
        ),
        fix="Depend on the port and wire the implementation in the composition root.",
        config=("roles.<role>.detect.implements", "architecture.composition_root"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        adapters = implementation_classes(ctx)
        if not adapters:
            return
        model = ctx.model
        roots = ctx.config.architecture.composition_root
        for syntax in ctx.syntax.sources():
            info = model.info(syntax.module)
            if info is None or info.kind is ModuleKind.COMPOSITION_ROOT:
                continue
            for node in syntax.walk():
                if not isinstance(node, ast.Call):
                    continue
                qualname = ctx.syntax.resolve(syntax, node.func)
                if qualname not in adapters:
                    continue
                role, cls = adapters[qualname]
                home = model.info(cls.module)
                if home is not None and (home.feature, home.layer) == (
                    info.feature,
                    info.layer,
                ):
                    continue
                where = info.layer or syntax.module
                yield node_violation(
                    self,
                    ctx,
                    syntax,
                    node.func,
                    f"{role} {cls.name} is constructed in {where}; construct it in "
                    f"the composition root ({join(roots, 'none configured')})",
                    target_module=cls.module,
                    expected={"composition_root": list(roots)},
                    hint=_port_hint(ctx, role, cls),
                )


class ConcreteDependency(BaseRule):
    code = "SMT202"
    name = "concrete-dependency"
    category = Category.CODE
    default_severity = Severity.WARNING
    requires = frozenset({Index.SYNTAX, Index.ROLES})
    doc = RuleDoc(
        summary="A parameter is annotated with a concrete adapter instead of its port.",
        rationale=(
            "An annotation couples the signature to one implementation even when the "
            "import hides behind TYPE_CHECKING or a shared re-export, so SMT101 never "
            "sees it."
        ),
        bad=(
            "class StartSession:\n"
            "    def __init__(self, sessions: SqlVoiceSessionRepository) -> None: ..."
        ),
        good=(
            "class StartSession:\n"
            "    def __init__(self, sessions: VoiceSessionRepository) -> None: ..."
        ),
        fix="Annotate the parameter with the port the adapter implements.",
        config=("roles.<role>.detect.implements", "roles.<role>.layers"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        adapters = implementation_classes(ctx)
        if not adapters:
            return
        model = ctx.model
        for syntax in ctx.syntax.sources():
            info = model.info(syntax.module)
            if info is None or not info.in_grid:
                continue
            for scope in scopes(syntax.tree):
                arguments = scope.function.args
                for arg in (
                    *arguments.posonlyargs,
                    *arguments.args,
                    *arguments.kwonlyargs,
                    *(a for a in (arguments.vararg, arguments.kwarg) if a),
                ):
                    for node in annotation_names(arg.annotation):
                        qualname = ctx.syntax.resolve(syntax, node)
                        if qualname not in adapters:
                            continue
                        role, cls = adapters[qualname]
                        if info.layer in ctx.config.roles[role].layers:
                            continue
                        ports = implemented_ports(ctx, role, cls)
                        yield node_violation(
                            self,
                            ctx,
                            syntax,
                            node,
                            f"parameter {arg.arg} of {scope.qualname} is annotated with "
                            f"{role} {cls.name}"
                            + (
                                f"; depend on {join([last_segment(p) for p in ports])}"
                                if ports
                                else ""
                            ),
                            target_module=cls.module,
                            expected={"ports": ports},
                            hint=_port_hint(ctx, role, cls),
                        )


def _port_hint(ctx: AnalysisContext, role: str, cls: ClassInfo) -> str:
    ports = [last_segment(p) for p in implemented_ports(ctx, role, cls)]
    port = join(ports, "its port")
    roots = ctx.config.architecture.composition_root
    root = roots[0] if roots else "the composition root"
    return f"Depend on {port} and wire {cls.name} in {root}."
