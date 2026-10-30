import hashlib
import io
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smelt.analysis.errors import AnalysisError


def _git(root: Path, *args: str, input_data: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=root,
            capture_output=True,
            input=input_data,
            check=False,
        )
    except OSError as exc:
        msg = f"changed/staged checks need git: {exc}"
        raise AnalysisError(msg) from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        msg = f"git {' '.join(args)} failed: {stderr}"
        raise AnalysisError(msg)
    return result.stdout


def _head(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.decode("ascii").strip()
    if result.returncode == 1:
        return None
    raise AnalysisError(result.stderr.decode("utf-8", errors="replace").strip())


def lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(path))  # noqa: PTH100 - captured Git paths must not follow unstaged symlinks


def git_repository(cwd: Path, requested: Path | None = None) -> Path:
    """Prefer the caller's repository, including staged directories deleted on disk."""
    try:
        repository = Path(
            os.fsdecode(_git(cwd, "rev-parse", "--show-toplevel")).strip()
        ).resolve()
    except AnalysisError:
        if requested is None:
            raise
    else:
        if requested is None or requested.is_relative_to(repository):
            return repository
    if requested is None:
        raise AnalysisError("changed/staged checks need a Git repository")
    anchor = next(
        (path for path in (requested.parent, *requested.parents) if path.is_dir()), cwd
    )
    return Path(
        os.fsdecode(_git(anchor, "rev-parse", "--show-toplevel")).strip()
    ).resolve()


@dataclass(frozen=True, slots=True)
class Comparison:
    """What ``--changed`` compared the working tree with."""

    base: str | None  # --base as given
    revision: str | None  # the commit compared with; None before the first commit
    head: str | None
    staged: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": "staged" if self.staged else "changed",
            "base": self.base,
            "merge_base": self.revision if self.base is not None else None,
            "compared_revision": self.revision,
            "head": self.head,
            "working_tree": not self.staged,
        }


@dataclass(frozen=True)
class GitIndex:
    repository: Path
    identity: str
    blobs: dict[str, str]


def capture_index(repository: Path) -> GitIndex:
    """Capture one immutable manifest, including stage/mode validation."""
    records = _git(repository, "ls-files", "--stage", "-z")
    blobs: dict[str, str] = {}
    for record in records.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_id, stage = metadata.split()
        path = Path(os.fsdecode(raw_path))
        _snapshot_path(repository, path)
        if stage != b"0":
            raise AnalysisError(f"--staged cannot check an unmerged index: {path}")
        if mode == b"120000":
            raise AnalysisError(f"--staged does not support symbolic links: {path}")
        if mode != b"160000":
            blobs[path.as_posix()] = object_id.decode("ascii")
    return GitIndex(repository, hashlib.sha256(records).hexdigest(), blobs)


def base_snapshot(
    root: Path, base: str | None, dest: Path
) -> tuple[Path | None, Comparison]:
    """Export the commit the working tree is compared with into ``dest``.

    That is ``HEAD``, or its merge-base with ``base``. Returns the project root
    inside the snapshot, or None when there is no commit to compare with yet, and
    the resolved revisions.
    """
    toplevel, comparison = resolve_comparison(root, base)
    return export_base(root, toplevel, comparison, dest), comparison


def resolve_comparison(
    root: Path,
    base: str | None,
    *,
    repository: Path | None = None,
) -> tuple[Path, Comparison]:
    toplevel = repository or Path(
        os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()
    )
    head = _head(root)
    if base is not None:
        ref = _git(root, "merge-base", head or "HEAD", base).decode().strip()
    elif head is not None:
        ref = head
    else:
        return toplevel, Comparison(base, None, None)
    return toplevel, Comparison(base, ref, head)


def export_base(
    root: Path, toplevel: Path, comparison: Comparison, dest: Path
) -> Path | None:
    ref = comparison.revision
    if ref is None:
        return None
    prefix = root.resolve().relative_to(toplevel.resolve())
    selected: list[tuple[str, Path]] = []
    # Read the tree itself: git archive can silently omit export-ignore sources
    # or substitute export-subst contents. Sibling workspace roots matter too.
    for record in _git(toplevel, "ls-tree", "-rz", "--full-tree", ref).split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        path = Path(os.fsdecode(raw_path))
        if kind != b"blob":
            continue
        if mode == b"120000":
            raise AnalysisError(
                f"changed/staged snapshots do not support symbolic links: {path}"
            )
        target = _snapshot_path(dest, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        selected.append((object_id.decode("ascii"), target))
    _copy_blobs(toplevel, selected)
    project = dest / prefix
    return project if project.is_dir() else None


def staged_snapshot(
    root: Path,
    dest: Path,
    *,
    config: Path | None = None,
    repository: Path | None = None,
    index: GitIndex | None = None,
) -> tuple[Path, str, dict[str, str]]:
    """Read immutable index blobs without checkout filters or worktree mutations."""
    repository = repository or Path(
        os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()
    )
    index = index or capture_index(repository)
    selected: list[tuple[str, Path]] = []
    blobs = index.blobs
    for relative, object_id in blobs.items():
        path = Path(relative)
        target = _snapshot_path(dest, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Source discovery only consumes Python, project metadata and the config.
        # Debt may have any filename, so its blob is copied on demand below.
        if (
            path.suffix == ".py"
            or path.name
            in {"pyproject.toml", ".python-version", "smelt.yaml", "smelt.yml"}
            or (config is not None and path == config.relative_to(repository))
        ):
            selected.append((object_id, target))
    _copy_blobs(repository, selected)
    return (
        dest / root.resolve().relative_to(repository.resolve()),
        index.identity,
        blobs,
    )


def _snapshot_path(dest: Path, path: Path) -> Path:
    # Snapshots start empty and never create symlinks. Validate the relative
    # components without a filesystem round trip for every repository file.
    if path.drive or path.is_absolute() or ".." in path.parts:
        raise AnalysisError(f"unsafe path in Git snapshot: {path}")
    return dest / path


def copy_index_file(
    repository: Path, relative: Path, target: Path, *, blobs: dict[str, str]
) -> None:
    """Copy an optional debt file from stage zero, including arbitrary filenames."""
    object_id = blobs.get(relative.as_posix())
    if object_id is None:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    _copy_blobs(repository, [(object_id, target)])


def _copy_blobs(repository: Path, files: list[tuple[str, Path]]) -> None:
    if not files:
        return
    contents = read_blobs(repository, list(dict.fromkeys(oid for oid, _ in files)))
    for object_id, path in files:
        path.write_bytes(contents[object_id])


def read_blobs(repository: Path, identifiers: list[str]) -> dict[str, bytes]:
    if not identifiers:
        return {}
    contents = io.BytesIO(
        _git(
            repository,
            "cat-file",
            "--batch",
            input_data="".join(f"{object_id}\n" for object_id in identifiers).encode(
                "ascii"
            ),
        )
    )
    result: dict[str, bytes] = {}
    for object_id in identifiers:
        header = contents.readline().split()
        if len(header) != 3 or header[:2] != [object_id.encode("ascii"), b"blob"]:  # noqa: PLR2004 - Git's SHA/type/size header
            raise AnalysisError("cannot read staged Git blob")
        size = int(header[2])
        data = contents.read(size)
        if len(data) != size or contents.read(1) != b"\n":
            raise AnalysisError("incomplete staged Git blob")
        result[object_id] = data
    return result
