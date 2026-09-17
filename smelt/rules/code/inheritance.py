from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.code.common import node_violation

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.syntax import ClassInfo, SyntaxIndex


def _matches(ancestor: str, forbidden: str) -> bool:
    """``pydantic.main.BaseModel`` matches ``pydantic.BaseModel`` (re-exported names)."""
    if ancestor == forbidden:
        return True
    head, _, name = forbidden.rpartition(".")
    top = head.split(".", 1)[0]
    return ancestor.startswith(f"{top}.") and ancestor.rsplit(".", 1)[-1] == name


class ForbiddenBaseClass(BaseRule):
    code = "SMT203"
    name = "forbidden-base-class"
    category = Category.CODE
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="A class in a layer inherits from one of the layer's forbid_bases.",
        rationale=(
            "Allowing an import is not the same as allowing a framework to shape your "
            "model. A domain entity that is a pydantic model or an ORM mapping carries "
            "validation and persistence concerns wherever it goes."
        ),
        bad="# voice/domain/session.py\nclass VoiceSession(BaseModel): ...",
        good="# voice/domain/session.py\n@dataclass\nclass VoiceSession: ...",
        fix=(
            "Keep the class plain and map it to framework models at the boundary "
            "(presentation or infrastructure)."
        ),
        config=("architecture.layers.<layer>.forbid_bases",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        model = ctx.model
        layers = model.layers
        if not any(layer.forbid_bases for layer in layers.values()):
            return
        for syntax in ctx.syntax.sources():
            info = model.info(syntax.module)
            if info is None or info.layer is None:
                continue
            forbidden = layers[info.layer].forbid_bases
            if not forbidden:
                continue
            for cls in syntax.classes:
                found = _forbidden_base(ctx.syntax, cls, forbidden)
                if found is None:
                    continue
                base, index, via = found
                node = cls.node.bases[index]
                indirect = f" (via {via})" if via else ""
                yield node_violation(
                    self,
                    ctx,
                    syntax,
                    node,
                    f"{info.layer} class {cls.name} must not inherit from {base}{indirect}",
                    target_module=base.rsplit(".", 1)[0],
                    expected={"forbid_bases": list(forbidden)},
                    hint=(
                        f"Keep {cls.name} free of framework bases and map it to "
                        f"{base.rsplit('.', 1)[-1]} at the boundary."
                    ),
                )


def _forbidden_base(
    syntax: SyntaxIndex, cls: ClassInfo, forbidden: list[str]
) -> tuple[str, int, str | None] | None:
    """(forbidden base, index of the direct base leading to it, intermediate class)."""
    for index, direct in enumerate(cls.bases):
        canonical = syntax.canonical(direct)
        if canonical is None:
            continue
        for entry in forbidden:
            if _matches(canonical, entry):
                return entry, index, None
        parent = syntax.classes.get(canonical)
        if parent is None:
            continue
        for ancestor in syntax.ancestors(parent):
            for entry in forbidden:
                if _matches(ancestor, entry):
                    return entry, index, canonical
    return None
