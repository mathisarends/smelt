import difflib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, assert_never

from smelt.diagnostics.violation import FileMove, FileWrite, LineEdit

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from smelt.diagnostics.violation import Edit, Violation


@dataclass(frozen=True, slots=True)
class FileDiff:
    """The whole content of one file before and after a plan is applied."""

    path: str
    before: str | None
    after: str | None
    renamed_from: str | None = None


@dataclass(slots=True)
class FixPlan:
    applied: list[Violation] = field(default_factory=list)
    skipped: list[Violation] = field(default_factory=list)
    diffs: list[FileDiff] = field(default_factory=list)


def _claims(edit: Edit) -> tuple[set[str], set[tuple[str, int]]]:
    """Return the paths and (path, line) pairs an edit takes ownership of."""
    match edit:
        case LineEdit(path=path, line=line):
            return set(), {(path, line)}
        case FileWrite(path=path):
            return {path}, set()
        case FileMove(source=source, destination=destination):
            return {source, destination}, set()
        case _:  # pragma: no cover - exhaustive
            assert_never(edit)


def _line_ending(line: str) -> str:
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return ending
    return "\n"


def _apply_line_edits(text: str, edits: list[LineEdit]) -> str:
    lines = text.splitlines(keepends=True)
    for edit in sorted(edits, key=lambda item: item.line, reverse=True):
        index = edit.line - 1
        if not 0 <= index < len(lines):
            continue
        if edit.text is None:
            del lines[index]
            continue
        ending = _line_ending(lines[index])
        lines[index] = edit.text.replace("\n", ending) + ending
    return "".join(lines)


class _Files:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[str, str | None] = {}

    def original(self, path: str) -> str | None:
        if path not in self._cache:
            file = self._root / path
            self._cache[path] = (
                file.read_text(encoding="utf-8", newline="") if file.is_file() else None
            )
        return self._cache[path]


def _diffs(root: Path, edits: list[Edit]) -> list[FileDiff]:
    files = _Files(root)
    lines: dict[str, list[LineEdit]] = defaultdict(list)
    writes: list[FileWrite] = []
    moves: list[FileMove] = []
    for edit in edits:
        match edit:
            case LineEdit():
                lines[edit.path].append(edit)
            case FileWrite():
                writes.append(edit)
            case FileMove():
                moves.append(edit)

    texts: dict[str, str | None] = {}
    for path, group in lines.items():
        text = files.original(path)
        if text is not None:
            texts[path] = _apply_line_edits(text, group)
    for write in writes:
        texts[write.path] = write.content
    renames: dict[str, str] = {}
    for move in moves:
        content = (
            texts[move.source] if move.source in texts else files.original(move.source)
        )
        if content is None:
            continue
        texts[move.destination] = content
        texts[move.source] = None
        renames[move.destination] = move.source

    moved = set(renames.values())
    diffs = []
    for path in sorted(texts):
        if path in moved:
            continue
        source = renames.get(path)
        before = files.original(source) if source else files.original(path)
        after = texts[path]
        # A pure rename leaves the content untouched but still has to be applied.
        if before != after or source is not None:
            diffs.append(FileDiff(path, before, after, renamed_from=source))
    return diffs


def build_plan(root: Path, violations: Iterable[Violation]) -> FixPlan:
    """Accept every fix that does not touch a file or line an earlier fix claimed."""
    plan = FixPlan()
    claimed_paths: set[str] = set()
    claimed_lines: set[tuple[str, int]] = set()
    accepted: list[Edit] = []
    for violation in violations:
        fix = violation.fix
        if fix is None:
            continue
        paths: set[str] = set()
        positions: set[tuple[str, int]] = set()
        for edit in fix.edits:
            edit_paths, edit_lines = _claims(edit)
            paths |= edit_paths
            positions |= edit_lines
        touched = {path for path, _ in positions}
        overlaps = (
            paths & claimed_paths
            or positions & claimed_lines
            or paths & {path for path, _ in claimed_lines}
            or touched & claimed_paths
        )
        if overlaps:
            plan.skipped.append(violation)
            continue
        claimed_paths |= paths
        claimed_lines |= positions
        accepted.extend(fix.edits)
        plan.applied.append(violation)
    plan.diffs = _diffs(root, accepted)
    return plan


def apply_plan(root: Path, plan: FixPlan) -> None:
    for diff in plan.diffs:
        target = root / diff.path
        if diff.after is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(diff.after, encoding="utf-8", newline="")
        if diff.renamed_from is not None:
            (root / diff.renamed_from).unlink(missing_ok=True)


def render_diff(diff: FileDiff) -> str:
    """Render one file change as a git-style unified diff."""
    source = diff.renamed_from or diff.path
    header = [f"diff --git a/{source} b/{diff.path}"]
    if diff.renamed_from is not None:
        header += [f"rename from {diff.renamed_from}", f"rename to {diff.path}"]
    body = difflib.unified_diff(
        (diff.before or "").splitlines(keepends=True),
        (diff.after or "").splitlines(keepends=True),
        fromfile="/dev/null" if diff.before is None else f"a/{source}",
        tofile="/dev/null" if diff.after is None else f"b/{diff.path}",
    )
    return "\n".join([*header, *(line.rstrip("\r\n") for line in body)])
