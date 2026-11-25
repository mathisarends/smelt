"""A staged project view whose source and policy both come from the Git index."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from smelt.analysis.errors import AnalysisError
from smelt.config import discover_config, load_config
from smelt.config.errors import ConfigError, ConfigIssue
from smelt.config.workspace import debt_scopes, member_directories
from smelt.engine.changes import (
    Comparison,
    capture_index,
    capture_repository,
    lexical_path,
    resolve_comparison,
)
from smelt.engine.git_source import GitSourceStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from smelt.config import LoadedConfig


@dataclass(frozen=True, slots=True)
class StagedProject:
    loaded: LoadedConfig
    original_config: Path
    repository: Path
    index_hash: str
    comparison: Comparison


@contextmanager
def staged_project(
    cwd: Path,
    config: str | None,
    *,
    base: str | None = None,
) -> Iterator[StagedProject]:
    requested = lexical_path(cwd / config) if config is not None else None
    # One Git process, or none with the native service, finds the repository and HEAD.
    captured = capture_repository(cwd, requested)
    repository = captured.path
    if requested is not None and not requested.is_relative_to(repository):
        raise AnalysisError("--staged config must be inside the Git repository")
    index = capture_index(repository)
    store = GitSourceStore.from_index(index)
    path = requested if requested is not None else discover_config(cwd, store=store)
    if path is None or not store.is_file(path) or not path.is_relative_to(repository):
        raise ConfigError(
            [ConfigIssue("", "no smelt.yml found in the staged Git index")]
        )
    loaded = load_config(path, store=store)
    if requested is None and loaded.config.project.workspace == "uv":
        owners = [
            m.path
            for m in member_directories(loaded.root, store=store)
            if m.skipped is None
            and store.resolve(cwd).is_relative_to(store.resolve(loaded.root / m.path))
        ]
        if owners:
            loaded = replace(loaded, member=max(owners, key=len))
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
    _, comparison = resolve_comparison(repository, base, captured=captured)
    yield StagedProject(loaded, loaded.path, repository, index.identity, comparison)
