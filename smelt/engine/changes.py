import subprocess
from pathlib import Path

from smelt.analysis.context import ChangeSet, FileChange
from smelt.analysis.parsing import AnalysisError


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError as exc:
        msg = f"--changed needs git: {exc}"
        raise AnalysisError(msg) from exc
    if result.returncode != 0:
        msg = f"git {' '.join(args)} failed: {result.stderr.strip()}"
        raise AnalysisError(msg)
    return result.stdout


def git_changes(root: Path, base: str | None = None) -> ChangeSet:
    """Changed files against ``base`` (merge-base), or staged + unstaged + untracked."""
    toplevel = Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    root = root.resolve()
    if base is not None:
        numstat = _git(root, "diff", "--numstat", "--no-renames", "--merge-base", base)
    else:
        has_head = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "HEAD"],  # noqa: S607
            cwd=root,
            capture_output=True,
            check=False,
        )
        if has_head.returncode == 0:
            numstat = _git(root, "diff", "--numstat", "--no-renames", "HEAD")
        else:
            numstat = _git(root, "diff", "--numstat", "--no-renames", "--cached")
            numstat += _git(root, "diff", "--numstat", "--no-renames")

    files: dict[str, FileChange] = {}
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:  # noqa: PLR2004
            continue
        added, deleted, name = parts
        rel = _relative(toplevel / name, root)
        if rel is None:
            continue
        previous = files.get(rel)
        count_added = int(added) if added.isdigit() else 0
        count_deleted = int(deleted) if deleted.isdigit() else 0
        if previous is not None:
            count_added += previous.added
            count_deleted += previous.deleted
        files[rel] = FileChange(rel, count_added, count_deleted)

    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "--full-name")
    for name in untracked.splitlines():
        path = toplevel / name
        rel = _relative(path, root)
        if rel is None or rel in files:
            continue
        try:
            lines = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            lines = 0
        files[rel] = FileChange(rel, lines, 0)
    return ChangeSet(dict(sorted(files.items())))


def _relative(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return None
