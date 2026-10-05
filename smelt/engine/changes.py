import io
import subprocess
import tarfile
from pathlib import Path

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


def base_snapshot(root: Path, base: str | None, dest: Path) -> Path | None:
    """Export the commit the working tree is compared with into ``dest``.

    That is ``HEAD``, or its merge-base with ``base``. Returns the project root inside
    the snapshot, or None when there is no commit to compare with yet.
    """
    toplevel = Path(_git(root, "rev-parse", "--show-toplevel").decode().strip())
    if base is not None:
        ref = _git(root, "merge-base", "HEAD", base).decode().strip()
    elif _has_head(root):
        ref = "HEAD"
    else:
        return None
    prefix = root.resolve().relative_to(toplevel.resolve()).as_posix()
    paths = [] if prefix == "." else [prefix]
    if paths and not _git(toplevel, "ls-tree", ref, "--", prefix).strip():
        return None  # the project directory is not committed yet
    archive = _git(toplevel, "archive", "--format=tar", ref, *paths)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")
    return dest if prefix == "." else dest / prefix
