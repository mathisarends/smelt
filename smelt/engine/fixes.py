"""Apply the fixes carried by visible findings: one engine for every rule."""

from __future__ import annotations

import ast
import difflib
from collections import defaultdict
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from smelt.analysis.parsing import AnalysisError
from smelt.diagnostics.fixes import Applicability
from smelt.engine.source_fixes import SourceEdit

if TYPE_CHECKING:
    from collections.abc import Callable

    from smelt.diagnostics.fixes import Fix, TextEdit
    from smelt.diagnostics.violation import Violation
    from smelt.engine.check import CheckOutcome

# A fix that overlaps an applied one is retried after the next check, this often.
_ROUNDS = 5
_OVERLAP = "overlaps another fix in the same run; run --fix again"
_UNSAFE = "behavior-changing fix requires --unsafe-fixes"


@dataclass(slots=True)
class FixReport:
    applied: int = 0
    files: list[str] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        unique = {tuple(sorted(item.items())): item for item in self.skipped}
        return {
            "applied": self.applied,
            "files": sorted(self.files),
            "skipped": list(unique.values()),
        }


def apply_fixes(
    outcome: CheckOutcome,
    recheck: Callable[[], CheckOutcome],
    *,
    unsafe: bool = False,
) -> tuple[CheckOutcome, FixReport]:
    """Write the permitted fixes of visible findings, then check again.

    Visible means after scope, selection, suppressions and accepted debt. A fix
    that overlaps one already applied waits for the check after the write.
    """
    report = FixReport()
    failed: list[dict[str, Any]] = []
    for _ in range(_ROUNDS):
        plans = _plan(outcome, unsafe=unsafe)
        # Skips describe the source as it is after the last write.
        report.skipped = [item for plan in plans for item in plan.skipped]
        deferred = [item for plan in plans for item in plan.deferred]
        wrote = False
        for plan in plans:
            if plan.edit is None:
                continue
            try:
                plan.edit.write(outcome.context.root)
            except (AnalysisError, OSError) as exc:
                failed.extend(_skip(v, str(exc)) for v in plan.applied)
                continue
            wrote = True
            report.applied += len(plan.applied)
            if plan.path not in report.files:
                report.files.append(plan.path)
        if wrote:
            outcome = recheck()
        if not (wrote and deferred):
            break
    report.skipped.extend(deferred)
    report.skipped.extend(failed)
    return outcome, report


def fix_diff(
    outcome: CheckOutcome, *, unsafe: bool = False, suggestions: bool = False
) -> str:
    """The permitted fixes of visible findings as a unified diff, without writing.

    `suggestions` adds the display fixes that are never applied, for review.
    """
    chunks: list[str] = []
    for plan in _plan(outcome, unsafe=unsafe, suggestions=suggestions):
        if plan.edit is None:
            continue
        before = _lines(plan.edit.original)
        after = _lines(plan.edit.replacement)
        chunks.extend(
            difflib.unified_diff(
                before, after, fromfile=f"a/{plan.path}", tofile=f"b/{plan.path}"
            )
        )
    return "".join(chunks)


@dataclass(slots=True)
class _FilePlan:
    path: str
    edit: SourceEdit | None = None
    applied: list[Violation] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    deferred: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Range:
    start: int
    end: int
    replacement: bytes

    def overlaps(self, other: _Range) -> bool:
        if self.start == self.end == other.start == other.end:
            return True
        return self.start < other.end and other.start < self.end


class _StaleEditError(AnalysisError):
    pass


def _plan(
    outcome: CheckOutcome, *, unsafe: bool, suggestions: bool = False
) -> list[_FilePlan]:
    allowed = {Applicability.SAFE} | ({Applicability.UNSAFE} if unsafe else set())
    if suggestions:
        allowed.add(Applicability.DISPLAY)
    by_path: dict[str, list[tuple[Violation, Fix]]] = defaultdict(list)
    plans: dict[str, _FilePlan] = {}
    seen: set[tuple[str, tuple[TextEdit, ...]]] = set()
    for violation in outcome.report.violations:
        fix, path = violation.fix, violation.path
        if fix is None or path is None:
            continue
        if fix.applicability is Applicability.DISPLAY and not suggestions:
            continue
        if fix.applicability not in allowed:
            plans.setdefault(path, _FilePlan(path)).skipped.append(
                _skip(violation, _UNSAFE)
            )
            continue
        # Several findings can share one repair, such as one ordering section.
        if (path, fix.edits) not in seen:
            seen.add((path, fix.edits))
            by_path[path].append((violation, fix))
    for path, fixes in by_path.items():
        plan = plans.setdefault(path, _FilePlan(path))
        try:
            _prepare(outcome, plan, fixes)
        except (AnalysisError, OSError, UnicodeError) as exc:
            plan.edit = None
            plan.skipped.extend(_skip(v, str(exc)) for v, _ in fixes)
            plan.applied.clear()
    return [plans[path] for path in sorted(plans)]


def _prepare(
    outcome: CheckOutcome, plan: _FilePlan, fixes: list[tuple[Violation, Fix]]
) -> None:
    ctx = outcome.context
    data = ctx.files.read_text(plan.path).encode("utf-8")
    starts = [0]
    starts.extend(index + 1 for index, byte in enumerate(data) if byte == ord("\n"))
    accepted: list[_Range] = []
    for violation, fix in sorted(fixes, key=lambda item: _first(item[1])):
        try:
            ranges = [_range(data, starts, edit) for edit in fix.edits]
        except _StaleEditError as exc:
            plan.skipped.append(_skip(violation, str(exc)))
            continue
        if any(new.overlaps(old) for new in ranges for old in accepted):
            plan.deferred.append(_skip(violation, _OVERLAP))
            continue
        accepted.extend(ranges)
        plan.applied.append(violation)
    if accepted:
        plan.edit = SourceEdit.prepare(
            ctx, plan.path, partial(_apply, ranges=tuple(accepted))
        )


def _first(fix: Fix) -> tuple[int, int]:
    return min((edit.line, edit.column) for edit in fix.edits)


def _range(data: bytes, starts: list[int], edit: TextEdit) -> _Range:
    start = _offset(data, starts, line=edit.line, column=edit.column)
    end = _offset(data, starts, line=edit.end_line, column=edit.end_column)
    if end < start or data[start:end] != edit.original.encode("utf-8"):
        raise _StaleEditError("source changed since the check; run the check again")
    return _Range(start, end, edit.replacement.encode("utf-8"))


def _offset(data: bytes, starts: list[int], *, line: int, column: int) -> int:
    if not 1 <= line <= len(starts):
        raise _StaleEditError("fix position is outside the source")
    limit = starts[line] if line < len(starts) else len(data)
    offset = starts[line - 1] + column - 1
    if not starts[line - 1] <= offset <= limit:
        raise _StaleEditError("fix position is outside the source")
    return offset


def _apply(text: str, *, ranges: tuple[_Range, ...]) -> str:
    crlf = "\r\n" in text
    normalized = text.replace("\r\n", "\n")
    if "\r" in normalized or (crlf and "\n" in text.replace("\r\n", "")):
        raise AnalysisError("mixed line endings require manual editing")
    data = normalized.encode("utf-8")
    for item in sorted(ranges, key=lambda item: item.start, reverse=True):
        data = data[: item.start] + item.replacement + data[item.end :]
    rewritten = data.decode("utf-8")
    try:
        ast.parse(rewritten)
    except SyntaxError as exc:
        raise AnalysisError(
            f"fixes would produce invalid Python ({exc.msg}); no replacement made"
        ) from exc
    return rewritten.replace("\n", "\r\n") if crlf else rewritten


def _skip(violation: Violation, reason: str) -> dict[str, Any]:
    return {
        "path": violation.path,
        "code": violation.code,
        "line": violation.line,
        "reason": reason,
    }


def _lines(data: bytes) -> list[str]:
    return data.decode("utf-8-sig").replace("\r\n", "\n").splitlines(keepends=True)
