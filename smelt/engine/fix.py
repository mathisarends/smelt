from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions, run_check
from smelt.fix import apply_plan, build_plan

if TYPE_CHECKING:
    from smelt.config import LoadedConfig
    from smelt.diagnostics.violation import Violation
    from smelt.fix import FileDiff

# A fix can unlock another one, but the loop must always terminate.
MAX_ROUNDS = 10


@dataclass(slots=True)
class FixOutcome:
    applied: list[Violation] = field(default_factory=list)
    skipped: list[Violation] = field(default_factory=list)
    diffs: list[FileDiff] = field(default_factory=list)

    @property
    def fixes(self) -> int:
        return len(self.applied)


def _selected(code: str, codes: tuple[str, ...]) -> bool:
    return not codes or any(
        code.startswith(prefix.strip().upper()) for prefix in codes if prefix.strip()
    )


def run_fix(
    loaded: LoadedConfig, codes: tuple[str, ...] = (), *, dry_run: bool = False
) -> FixOutcome:
    """Apply every fixable violation `smelt check` reports, in rounds until stable.

    The check always runs with the full rule selection so that fixing one code
    never changes how another rule sees the project; `codes` only narrows which
    of the reported violations are fixed.
    """
    outcome = FixOutcome()
    for _ in range(MAX_ROUNDS):
        report = run_check(loaded, CheckOptions()).report
        targets = [
            violation
            for violation in report.violations
            if violation.fix is not None and _selected(violation.code, codes)
        ]
        plan = build_plan(loaded.root, targets)
        outcome.skipped = plan.skipped
        if dry_run:
            outcome.applied = plan.applied
            outcome.diffs = plan.diffs
            return outcome
        if not plan.applied:
            return outcome
        apply_plan(loaded.root, plan)
        outcome.applied.extend(plan.applied)
        outcome.diffs.extend(plan.diffs)
        if not plan.skipped:
            return outcome
    return outcome
