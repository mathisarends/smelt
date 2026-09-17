from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import ModuleKind
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import display_module_path, join, role_home

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.syntax import ClassInfo


def class_violation(  # noqa: PLR0913
    rule: BaseRule,
    ctx: AnalysisContext,
    cls: ClassInfo,
    message: str,
    *,
    expected: dict[str, object] | None = None,
    hint: str | None = None,
) -> Violation:
    info = ctx.model.info(cls.module)
    start, end = cls.name_span
    return rule.violation(
        message,
        path=cls.path,
        line=cls.line,
        column=start,
        end_column=end,
        source_module=cls.module,
        feature=info.feature if info else None,
        layer=info.layer if info else None,
        expected=expected,
        hint=hint,
    )


class MisplacedRole(BaseRule):
    code = "SMT204"
    name = "misplaced-role"
    category = Category.CODE
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX, Index.ROLES})
    doc = RuleDoc(
        summary="A class with a role lives in a layer outside roles.<role>.layers.",
        rationale=(
            "Roles give concepts a canonical home. A port defined next to its adapter, or "
            "an adapter in the application layer, drags infrastructure concerns inward "
            "and makes the concept hard to find."
        ),
        bad=(
            "# voice/infra/sql.py\n"
            "class VoiceSessionRepository(Protocol):\n"
            "    def save(self, session: VoiceSession) -> None: ..."
        ),
        good=(
            "# voice/application/ports.py\n"
            "class VoiceSessionRepository(Protocol):\n"
            "    def save(self, session: VoiceSession) -> None: ..."
        ),
        fix="Move the class into one of the role's layers; `smelt where <role>` prints the path.",
        config=("roles.<role>.layers", "roles.<role>.detect"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        model = ctx.model
        for match in ctx.roles.matches():
            role = ctx.config.roles[match.role]
            if not role.layers:
                continue
            info = model.info(match.cls.module)
            if info is None or info.kind is not ModuleKind.FEATURE:
                continue
            if info.layer in role.layers:
                continue
            home = role_home(model, match.role, info.feature)
            expected_path = (
                display_module_path(model, home[0], package=home[1]) if home else None
            )
            where = f"in {info.layer}" if info.layer else "outside any layer"
            yield class_violation(
                self,
                ctx,
                match.cls,
                f"{match.role} {match.cls.name} is defined {where}; "
                f"{match.role}s belong in {join(role.layers)}",
                expected={
                    "layers": list(role.layers),
                    **({"path": expected_path} if expected_path else {}),
                },
                hint=(
                    f"Move {match.cls.name} to {expected_path}."
                    if expected_path
                    else f"Move {match.cls.name} into {join(role.layers)}."
                ),
            )
