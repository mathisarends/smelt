"""A staged project view whose source and policy both come from the Git index."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from smelt.analysis.errors import AnalysisError
from smelt.config import discover_config, load_config
from smelt.config.errors import ConfigError, ConfigIssue
from smelt.engine.changes import _git, copy_index_file, staged_snapshot

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.config import LoadedConfig


@dataclass(frozen=True)
class StagedProject:
    loaded: LoadedConfig
    original_config: Path
    repository: Path
    index_hash: str


@contextmanager
def staged_project(cwd: Path, config: str | None) -> Iterator[StagedProject]:
    requested = (cwd / config).resolve() if config is not None else None
    repository = Path(
        os.fsdecode(
            _git(requested.parent if requested else cwd, "rev-parse", "--show-toplevel")
        ).strip()
    ).resolve()
    if requested is not None and not requested.is_relative_to(repository):
        raise AnalysisError("--staged config must be inside the Git repository")
    with tempfile.TemporaryDirectory(prefix="smelt-index-") as tmp:
        snapshot = Path(tmp).resolve()
        _, index_hash, blobs = staged_snapshot(
            repository, snapshot, config=requested, repository=repository
        )
        path = (
            snapshot / requested.relative_to(repository)
            if requested is not None
            else discover_config(snapshot / cwd.resolve().relative_to(repository))
        )
        if path is None or not path.is_file() or not path.is_relative_to(snapshot):
            raise ConfigError(
                [ConfigIssue("", "no smelt.yaml found in the staged Git index")]
            )
        loaded = load_config(path)
        for directory in [
            *loaded.config.project.source_roots,
            *loaded.config.project.test_roots,
        ]:
            if not (loaded.root / directory).resolve().is_relative_to(snapshot):
                raise AnalysisError(
                    "--staged source/test roots must be inside the Git repository"
                )
        if loaded.config.debt:
            debt = (loaded.root / loaded.config.debt).resolve()
            if not debt.is_relative_to(snapshot):
                raise AnalysisError(
                    "--staged debt file must be inside the Git repository"
                )
            copy_index_file(repository, debt.relative_to(snapshot), debt, blobs=blobs)
        yield StagedProject(
            loaded, repository / path.relative_to(snapshot), repository, index_hash
        )
