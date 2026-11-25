from __future__ import annotations

from copy import copy
from enum import StrEnum
from functools import cached_property
from typing import TYPE_CHECKING

from smelt.config.source import SourceStore

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.analysis.contracts import ContractIndex
    from smelt.analysis.coverage import CoverageIndex
    from smelt.analysis.files import FileIndex
    from smelt.analysis.python_version import PythonTargetIndex
    from smelt.analysis.workspace_policy import WorkspaceIndex
    from smelt.config.models import SmeltConfig
    from smelt.config.workspace import ArchitectureScope
    from smelt.model import ArchitectureModel


class Index(StrEnum):
    FILES = "files"
    IMPORTS = "imports"
    MEMBERS = "member_usage"
    MODEL = "model"
    ORDERING = "ordering"
    SYNTAX = "syntax"
    TESTS = "test_syntax"
    WORKSPACE = "workspace"


class AnalysisContext:
    def __init__(
        self,
        root: Path,
        config: SmeltConfig,
        *,
        store: SourceStore | None = None,
    ) -> None:
        # Paths below the root compare with the resolved root the core reports.
        self.store = store or SourceStore()
        self.root = self.store.resolve(root)
        self.config = config

    def architecture_view(self, scope: ArchitectureScope) -> AnalysisContext:
        """A view in which one scope's architecture is the project architecture."""
        model = self.model.parts[self.architecture_scopes.index(scope)]
        view = copy(self)
        view.config = scope.config
        view.model = model
        return view

    @cached_property
    def files(self) -> FileIndex:
        # Load indexes only when the selected checks need them.
        from smelt.analysis.files import FileIndex  # noqa: PLC0415

        return FileIndex.discover(self.root, self.config, store=self.store)

    @cached_property
    def architecture_scopes(self) -> tuple[ArchitectureScope, ...]:
        """The independent architectures of a workspace, including the root."""
        from smelt.config.workspace import architecture_scopes  # noqa: PLC0415

        return architecture_scopes(self.config)

    @cached_property
    def model(self) -> ArchitectureModel:
        from smelt.model import ArchitectureModel  # noqa: PLC0415

        if not self.architecture_scopes:
            return ArchitectureModel.build(
                self.config, self.files.sources, self.files.packages
            )
        return ArchitectureModel.combine(
            self.config,
            tuple(
                ArchitectureModel.build(
                    scope.config,
                    self.files.sources,
                    self.files.packages,
                    scope=scope.packages,
                )
                for scope in self.architecture_scopes
            ),
        )

    @cached_property
    def coverage(self) -> CoverageIndex:
        """The policy coverage, which only a native core run inventories."""
        from smelt.analysis.errors import AnalysisError  # noqa: PLC0415

        msg = "policy coverage comes from a native core run of the project"
        raise AnalysisError(msg)

    @cached_property
    def workspace(self) -> WorkspaceIndex:
        from smelt.analysis.workspace_policy import WorkspaceIndex  # noqa: PLC0415

        return WorkspaceIndex(self.files)

    @cached_property
    def contracts(self) -> ContractIndex:
        from smelt.analysis.contracts import ContractIndex  # noqa: PLC0415

        return ContractIndex(self.files)

    @cached_property
    def python_targets(self) -> PythonTargetIndex:
        from smelt.analysis.python_version import PythonTargetIndex  # noqa: PLC0415

        return PythonTargetIndex(self.files, self.store)
