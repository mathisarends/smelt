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
    from smelt.analysis.facade_usage import FacadeUsage
    from smelt.analysis.files import FileIndex
    from smelt.analysis.imports import ImportDetail, ImportIndex
    from smelt.analysis.members import MemberUsageIndex
    from smelt.analysis.ordering import DefinitionOrderIndex
    from smelt.analysis.parsing import AstCache
    from smelt.analysis.python_version import PythonTargetIndex
    from smelt.analysis.syntax import SyntaxIndex
    from smelt.analysis.test_syntax import TestSyntaxIndex
    from smelt.analysis.workspace_policy import WorkspaceIndex
    from smelt.config.models import SmeltConfig
    from smelt.config.workspace import ArchitectureScope
    from smelt.model import ArchitectureModel


class Index(StrEnum):
    COVERAGE = "coverage"
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
        # grimp resolves its search paths; the file index must agree on the prefix
        self.store = store or SourceStore()
        self.root = self.store.resolve(root)
        self.config = config

    def architecture_view(self, scope: ArchitectureScope) -> AnalysisContext:
        """A view in which one scope's architecture is the project architecture."""
        model = self.model.parts[self.architecture_scopes.index(scope)]
        view = copy(self)
        view.config = scope.config
        view.model = model
        view.__dict__.pop("coverage", None)
        return view

    @cached_property
    def files(self) -> FileIndex:
        # Load indexes only when the selected checks need them.
        from smelt.analysis.files import FileIndex  # noqa: PLC0415

        return FileIndex.discover(self.root, self.config, store=self.store)

    @cached_property
    def asts(self) -> AstCache:
        from smelt.analysis.parsing import AstCache  # noqa: PLC0415

        return AstCache(self.files)

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
        from smelt.analysis.coverage import CoverageIndex  # noqa: PLC0415

        return CoverageIndex.build(self.files, self.model, self.asts)

    @cached_property
    def workspace(self) -> WorkspaceIndex:
        from smelt.analysis.workspace_policy import WorkspaceIndex  # noqa: PLC0415

        return WorkspaceIndex(self.files)

    @cached_property
    def imports(self) -> ImportIndex:
        from smelt.analysis.imports import ImportIndex  # noqa: PLC0415

        return ImportIndex.build(self.files, self.asts)

    @cached_property
    def package_api_imports(self) -> tuple[tuple[str, ImportDetail], ...]:
        """Source and test callers shared by all package API policies."""
        sources = self.files.sources
        callers = [
            (sources[detail.importer].path, detail)
            for detail in self.imports.all_imports()
        ]
        callers.extend(self.imports.imports_in_tests(self.syntax))
        return tuple(callers)

    @cached_property
    def syntax(self) -> SyntaxIndex:
        from smelt.analysis.syntax import SyntaxIndex  # noqa: PLC0415

        return SyntaxIndex(self.files, self.asts)

    @cached_property
    def facade_usage(self) -> FacadeUsage:
        """Facade demand shared by the export, fan-out and ownership checks."""
        from smelt.analysis.facade_usage import FacadeUsage  # noqa: PLC0415

        return FacadeUsage(self.syntax, self.imports, files=self.files)

    @cached_property
    def member_usage(self) -> MemberUsageIndex:
        from smelt.analysis.members import MemberUsageIndex  # noqa: PLC0415

        return MemberUsageIndex(self.syntax)

    @cached_property
    def test_syntax(self) -> TestSyntaxIndex:
        from smelt.analysis.test_syntax import TestSyntaxIndex  # noqa: PLC0415

        return TestSyntaxIndex(self.files, self.asts, syntax=self.syntax)

    @cached_property
    def contracts(self) -> ContractIndex:
        from smelt.analysis.contracts import ContractIndex  # noqa: PLC0415

        return ContractIndex(self.files)

    @cached_property
    def python_targets(self) -> PythonTargetIndex:
        from smelt.analysis.python_version import PythonTargetIndex  # noqa: PLC0415

        return PythonTargetIndex(self.files, self.store)

    @cached_property
    def ordering(self) -> DefinitionOrderIndex:
        from smelt.analysis.ordering import DefinitionOrderIndex  # noqa: PLC0415

        return DefinitionOrderIndex(
            self.files,
            self.contracts,
            self.asts,
            python_targets=self.python_targets,
        )

    def ensure(self, indexes: frozenset[Index]) -> None:
        """Build the requested indexes up front so failures surface before rules run."""
        for index in sorted(indexes):
            getattr(self, index.value)
