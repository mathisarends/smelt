from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.fix.move_class import move_class_fix
from smelt.model import ModuleKind, is_within
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.code.roles import class_violation
from smelt.rules.common import display_module_path

if TYPE_CHECKING:
    from collections.abc import Iterator


class RoleFile(BaseRule):
    code = "SMT303"
    name = "role-file"
    category = Category.STRUCTURE
    default_severity = Severity.ERROR
    requires = frozenset({Index.SYNTAX, Index.ROLES})
    fixable = True
    doc = RuleDoc(
        summary="A class with a role lives in a different module than roles.<role>.file.",
        rationale=(
            "When every port of a feature lives in ports.py, readers and agents find them "
            "without searching, and reviews see new ports as a distinct change."
        ),
        bad=(
            "# voice/application/session.py\n"
            "class VoiceSessionRepository(Protocol): ..."
        ),
        good=(
            "# voice/application/ports.py\nclass VoiceSessionRepository(Protocol): ..."
        ),
        fix="Move the class into the role's file and update its imports.",
        config=("roles.<role>.file",),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        model = ctx.model
        for match in ctx.roles.matches():
            role = ctx.config.roles[match.role]
            module_name = role.module_name
            info = model.info(match.cls.module)
            if module_name is None or info is None or info.layer is None:
                continue
            if info.kind is not ModuleKind.FEATURE or info.layer not in role.layers:
                continue
            package = model.layer_package(info.feature, info.layer)
            if package is None:
                continue
            expected = f"{package}.{module_name}"
            if is_within(match.cls.module, expected):
                continue
            expected_path = display_module_path(model, expected)
            yield class_violation(
                self,
                ctx,
                match.cls,
                f"{match.role} {match.cls.name} must be defined in {expected_path}",
                expected={"path": expected_path},
                hint=f"Move {match.cls.name} to {expected_path} and update its imports.",
                fix=move_class_fix(ctx, match.cls, expected),
            )
