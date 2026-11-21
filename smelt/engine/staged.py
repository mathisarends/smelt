"""A staged project view whose source and policy both come from the Git index."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from smelt.analysis.cache import FactCache, cache_directory
from smelt.analysis.errors import AnalysisError
from smelt.config import discover_config, load_config
from smelt.config.errors import ConfigError, ConfigIssue
from smelt.config.workspace import debt_scopes, member_directories
from smelt.engine.changes import capture_index, git_repository, lexical_path
from smelt.engine.git_source import GitSourceStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from smelt.config import LoadedConfig
    from smelt.engine.changes import GitIndex


@dataclass(frozen=True, slots=True)
class StagedProject:
    loaded: LoadedConfig
    original_config: Path
    repository: Path
    index_hash: str


@contextmanager
def staged_project(
    cwd: Path,
    config: str | None,
    *,
    index: GitIndex | None = None,
    use_cache: bool = False,
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
            [ConfigIssue("", "no smelt.yml found in the staged Git index")]
        )
    # Policy is read before run_check creates the analysis cache. Reuse verified
    # immutable blobs here too, instead of starting cat-file for the same config
    # after every source edit. Close this transaction before analysis opens its
    # own cache connection, including when policy validation fails.
    cache = FactCache(cache_directory(path.parent)) if use_cache else None
    store.cache = cache
    try:
        loaded = load_config(path, store=store)
        if requested is None and loaded.config.project.workspace == "uv":
            owners = [
                m.path
                for m in member_directories(loaded.root, store=store)
                if m.skipped is None
                and store.resolve(cwd).is_relative_to(
                    store.resolve(loaded.root / m.path)
                )
            ]
            if owners:
                loaded = replace(loaded, member=max(owners, key=len))
    finally:
        if cache is not None:
            cache.close()
        store.cache = None
    for directory in [
        *loaded.config.project.source_roots,
        *loaded.config.project.test_roots,
    ]:
        if not store.resolve(loaded.root / directory).is_relative_to(repository):
            raise AnalysisError(
                "--staged source/test roots must be inside the Git repository"
            )
    if any(
        not store.resolve(loaded.root / scope.path).is_relative_to(repository)
        for scope in debt_scopes(loaded.config)
    ):
        raise AnalysisError("--staged debt file must be inside the Git repository")
    yield StagedProject(loaded, loaded.path, repository, index.identity)
