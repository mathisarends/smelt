from __future__ import annotations

from copy import copy
from dataclasses import replace
from enum import StrEnum
from functools import cached_property
from typing import TYPE_CHECKING

from smelt.config.source import SourceStore

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.analysis.cache import FactCache
    from smelt.analysis.contracts import ContractIndex
    from smelt.analysis.coverage import CoverageIndex
    from smelt.analysis.files import FileIndex, SourceFile
    from smelt.analysis.imports import ImportIndex
    from smelt.analysis.members import MemberUsageIndex
    from smelt.analysis.ordering import DefinitionOrderIndex
    from smelt.analysis.parsing import AstCache
    from smelt.analysis.python_version import PythonTargetIndex
    from smelt.analysis.syntax import SyntaxIndex
    from smelt.analysis.test_syntax import TestSyntaxIndex
    from smelt.config.models import SmeltConfig
    from smelt.model import ArchitectureModel


class Index(StrEnum):
    COVERAGE = "coverage"
    FILES = "files"
    IMPORTS = "imports"
    MEMBERS = "member_usage"
    ORDERING = "ordering"
    SYNTAX = "syntax"
    TESTS = "test_syntax"


class AnalysisContext:
    def __init__(
        self,
        root: Path,
        config: SmeltConfig,
        *,
        cache: FactCache | None = None,
        store: SourceStore | None = None,
    ) -> None:
        # grimp resolves its search paths; the file index must agree on the prefix
        self.store = store or SourceStore()
        self.root = self.store.resolve(root)
        self.config = config
        self.cache = cache

    def file_view(self, source: SourceFile) -> AnalysisContext:
        """A source-loop view for rules explicitly declaring file-local inputs."""
        view = copy(self)
        view.files = replace(self.files, sources={source.module: source}, tests={})
        return view

    def test_view(self, path: str) -> AnalysisContext:
        view = copy(self)
        view.test_syntax = self.test_syntax.for_file(path)
        return view

    @cached_property
    def files(self) -> FileIndex:
        from smelt.analysis.files import FileIndex  # noqa: PLC0415 - load only requested analysis indexes

        return FileIndex.discover(self.root, self.config, store=self.store)

    @cached_property
    def asts(self) -> AstCache:
        from smelt.analysis.parsing import AstCache  # noqa: PLC0415 - load only requested analysis indexes

        return AstCache(self.files, cache=self.cache)

    @cached_property
    def model(self) -> ArchitectureModel:
        from smelt.model import ArchitectureModel  # noqa: PLC0415 - load only requested analysis indexes

        return ArchitectureModel.build(
            self.config, self.files.sources, self.files.packages
        )

    @cached_property
    def coverage(self) -> CoverageIndex:
        from smelt.analysis.coverage import CoverageIndex  # noqa: PLC0415 - load only requested analysis indexes

        return CoverageIndex.build(self.files, self.model, self.asts)

    @cached_property
    def imports(self) -> ImportIndex:
        from smelt.analysis.imports import ImportIndex  # noqa: PLC0415 - load only requested analysis indexes

        return ImportIndex.build(self.files, self.asts, cache=self.cache)

    @cached_property
    def syntax(self) -> SyntaxIndex:
        from smelt.analysis.syntax import SyntaxIndex  # noqa: PLC0415 - load only requested analysis indexes

        return SyntaxIndex(self.files, self.asts, cache=self.cache)

    @cached_property
    def member_usage(self) -> MemberUsageIndex:
        from smelt.analysis.members import MemberUsageIndex  # noqa: PLC0415 - load only requested analysis indexes

        return MemberUsageIndex(self.syntax)

    @cached_property
    def test_syntax(self) -> TestSyntaxIndex:
        from smelt.analysis.test_syntax import TestSyntaxIndex  # noqa: PLC0415 - load only requested analysis indexes

        return TestSyntaxIndex(
            self.files, self.asts, cache=self.cache, syntax=self.syntax
        )

    @cached_property
    def contracts(self) -> ContractIndex:
        from smelt.analysis.contracts import ContractIndex  # noqa: PLC0415 - load only requested analysis indexes

        return ContractIndex(self.files)

    @cached_property
    def python_targets(self) -> PythonTargetIndex:
        from smelt.analysis.python_version import PythonTargetIndex  # noqa: PLC0415 - load only requested analysis indexes

        return PythonTargetIndex(self.files)

    @cached_property
    def ordering(self) -> DefinitionOrderIndex:
        from smelt.analysis.ordering import DefinitionOrderIndex  # noqa: PLC0415 - load only requested analysis indexes

        return DefinitionOrderIndex(
            self.files, self.contracts, self.asts, self.python_targets
        )

    def ensure(self, indexes: frozenset[Index]) -> None:
        """Build the requested indexes up front so failures surface before rules run."""
        for index in sorted(indexes):
            getattr(self, index.value)
