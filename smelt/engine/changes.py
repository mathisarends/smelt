import io
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smelt.analysis.parsing import AnalysisError


def _git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        msg = f"--changed needs git: {exc}"
        raise AnalysisError(msg) from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        msg = f"git {' '.join(args)} failed: {stderr}"
        raise AnalysisError(msg)
    return result.stdout


def _has_head(root: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


@dataclass(frozen=True, slots=True)
class Comparison:
    """What ``--changed`` compared the working tree with."""

    base: str | None  # --base as given
    revision: str | None  # the commit compared with; None before the first commit
    head: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": "changed",
            "base": self.base,
            "merge_base": self.revision if self.base is not None else None,
            "compared_revision": self.revision,
            "head": self.head,
            "working_tree": True,
        }


def base_snapshot(
    root: Path, base: str | None, dest: Path
) -> tuple[Path | None, Comparison]:
    """Export the commit the working tree is compared with into ``dest``.

    That is ``HEAD``, or its merge-base with ``base``. Returns the project root
    inside the snapshot, or None when there is no commit to compare with yet, and
    the resolved revisions.
    """
    toplevel = Path(_git(root, "rev-parse", "--show-toplevel").decode().strip())
    head = _git(root, "rev-parse", "HEAD").decode().strip() if _has_head(root) else None
    if base is not None:
        ref = _git(root, "merge-base", "HEAD", base).decode().strip()
    elif head is not None:
        ref = head
    else:
        return None, Comparison(base, None, None)
    comparison = Comparison(base, ref, head)
    prefix = root.resolve().relative_to(toplevel.resolve()).as_posix()
    paths = [] if prefix == "." else [prefix]
    if paths and not _git(toplevel, "ls-tree", ref, "--", prefix).strip():
        return None, comparison  # the project directory is not committed yet
    archive = _git(toplevel, "archive", "--format=tar", ref, *paths)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    return (dest if prefix == "." else dest / prefix), comparison
