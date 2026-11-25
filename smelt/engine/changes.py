import hashlib
import io
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smelt.analysis.errors import AnalysisError
from smelt.engine import native_git


def lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(path))  # noqa: PTH100 - captured Git paths must not follow unstaged symlinks


def _head(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"],  # noqa: S607 -- Use the Git executable selected by PATH.
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.decode("ascii").strip()
    if result.returncode == 1:
        return None
    raise AnalysisError(result.stderr.decode("utf-8", errors="replace").strip())


@dataclass(frozen=True, slots=True)
class GitRepository:
    path: Path
    head: str | None


def capture_repository(cwd: Path, requested: Path | None = None) -> GitRepository:
    """Read repository location and HEAD with one Git process, including unborn HEAD."""

    def inspect(anchor: Path) -> GitRepository:
        native = native_git.capture(anchor)
        if native is not None:
            return GitRepository(*native)
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel", "--verify", "--quiet", "HEAD"],  # noqa: S607 -- Use the Git executable selected by PATH.
            cwd=anchor,
            capture_output=True,
            check=False,
        )
        if result.returncode not in {0, 1} or not result.stdout:
            raise AnalysisError(result.stderr.decode("utf-8", errors="replace").strip())
        output = result.stdout.rstrip(b"\n")
        if result.returncode == 1:
            return GitRepository(Path(os.fsdecode(output)).resolve(), None)
        root, head = output.rsplit(b"\n", 1)
        return GitRepository(Path(os.fsdecode(root)).resolve(), head.decode("ascii"))

    try:
        captured = inspect(cwd)
    except AnalysisError:
        if requested is None:
            raise
    else:
        if requested is None or requested.is_relative_to(captured.path):
            return captured
    if requested is None:
        raise AnalysisError("changed/staged checks need a Git repository")
    anchor = next(
        (path for path in (requested.parent, *requested.parents) if path.is_dir()), cwd
    )
    return inspect(anchor)


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


@dataclass(frozen=True, slots=True)
class GitIndex:
    repository: Path
    identity: str
    blobs: dict[str, str]
    yaml_paths: tuple[str, ...] | None = field(default=None, compare=False, repr=False)


def capture_index(repository: Path) -> GitIndex:
    """Capture one immutable manifest, including stage/mode validation."""
    records = native_git.index_records(repository)
    if records is None:
        records = _run_git(repository, "ls-files", "--stage", "-z")
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
        # A link blob holds the target name, not a copy of its contents.
        if mode not in {b"120000", b"160000"}:
            blobs[path.as_posix()] = object_id.decode("ascii")
    return GitIndex(
        repository,
        hashlib.sha256(records).hexdigest(),
        blobs,
        native_git.yaml_inventory(repository, records),
    )


def resolve_comparison(
    root: Path,
    base: str | None,
    *,
    repository: Path | None = None,
    captured: GitRepository | None = None,
) -> tuple[Path, Comparison]:
    toplevel = (captured.path if captured else repository) or Path(
        os.fsdecode(_run_git(root, "rev-parse", "--show-toplevel")).strip()
    )
    head = captured.head if captured is not None else _head(root)
    if base is not None:
        ref = _run_git(root, "merge-base", head or "HEAD", base).decode().strip()
    elif head is not None:
        ref = head
    else:
        return toplevel, Comparison(base, None, None)
    return toplevel, Comparison(base, ref, head)


def read_blobs(repository: Path, identifiers: list[str]) -> dict[str, bytes]:
    if not identifiers:
        return {}
    native = native_git.read_blobs(repository, identifiers)
    if native is not None:
        return native
    contents = io.BytesIO(
        _run_git(
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


def _run_git(root: Path, *args: str, input_data: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(  # noqa: S603 -- Git receives a list of arguments without shell expansion.
            ["git", *args],  # noqa: S607 -- Use the Git executable selected by PATH.
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


def _snapshot_path(dest: Path, path: Path) -> Path:
    # Snapshots start empty and never create symlinks. Validate the relative
    # components without a filesystem round trip for every repository file.
    if path.drive or path.is_absolute() or ".." in path.parts:
        raise AnalysisError(f"unsafe path in Git snapshot: {path}")
    return dest / path
