"""A staged project view whose source and policy both come from the Git index."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smelt.analysis.errors import AnalysisError
from smelt.config import discover_config, load_config
from smelt.config.errors import ConfigError, ConfigIssue
from smelt.engine.changes import capture_index, git_repository, lexical_path
from smelt.engine.git_source import GitSourceStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from smelt.config import LoadedConfig
    from smelt.engine.changes import GitIndex


@dataclass(frozen=True)
class StagedProject:
    loaded: LoadedConfig
    original_config: Path
    repository: Path
    index_hash: str


@contextmanager
def staged_project(
    cwd: Path, config: str | None, *, index: GitIndex | None = None
) -> Iterator[StagedProject]:
    requested = lexical_path(cwd / config) if config is not None else None
    repository = index.repository if index else git_repository(cwd, requested)
    if requested is not None and not requested.is_relative_to(repository):
        raise AnalysisError("--staged config must be inside the Git repository")
    index = index or capture_index(repository)
    store = GitSourceStore.from_index(index)
    path = requested if requested is not None else discover_config(cwd, store=store)
    if path is None or not store.is_file(path) or not path.is_relative_to(repository):
        raise ConfigError(
            [ConfigIssue("", "no smelt.yaml found in the staged Git index")]
        )
    loaded = load_config(path, store=store)
    for directory in [
        *loaded.config.project.source_roots,
        *loaded.config.project.test_roots,
    ]:
        if not store.resolve(loaded.root / directory).is_relative_to(repository):
            raise AnalysisError(
                "--staged source/test roots must be inside the Git repository"
            )
    if loaded.config.debt and not store.resolve(
        loaded.root / loaded.config.debt
    ).is_relative_to(repository):
        raise AnalysisError("--staged debt file must be inside the Git repository")
    yield StagedProject(loaded, path, repository, index.identity)
