from __future__ import annotations

from enum import StrEnum
from functools import cached_property
from typing import TYPE_CHECKING

from smelt.analysis.contracts import ContractIndex
from smelt.analysis.coverage import CoverageIndex
from smelt.analysis.files import FileIndex
from smelt.analysis.imports import ImportIndex
from smelt.analysis.ordering import DefinitionOrderIndex
from smelt.analysis.parsing import AstCache
from smelt.analysis.python_version import PythonTargetIndex
from smelt.analysis.syntax import SyntaxIndex
from smelt.analysis.test_syntax import TestSyntaxIndex
from smelt.model import ArchitectureModel

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.config.models import SmeltConfig


class Index(StrEnum):
    COVERAGE = "coverage"
    FILES = "files"
    IMPORTS = "imports"
    ORDERING = "ordering"
    SYNTAX = "syntax"
    TESTS = "test_syntax"


class AnalysisContext:
    def __init__(self, root: Path, config: SmeltConfig) -> None:
        # grimp resolves its search paths; the file index must agree on the prefix
        self.root = root.resolve()
        self.config = config

    @cached_property
    def files(self) -> FileIndex:
        return FileIndex.discover(self.root, self.config)

    @cached_property
    def asts(self) -> AstCache:
        return AstCache(self.files)

    @cached_property
    def model(self) -> ArchitectureModel:
        return ArchitectureModel.build(
            self.config, self.files.sources, self.files.packages
        )

    @cached_property
    def coverage(self) -> CoverageIndex:
        return CoverageIndex.build(self.files, self.model, self.asts)

    @cached_property
    def imports(self) -> ImportIndex:
        return ImportIndex.build(self.files, self.asts)

    @cached_property
    def syntax(self) -> SyntaxIndex:
        return SyntaxIndex(self.files, self.asts)

    @cached_property
    def test_syntax(self) -> TestSyntaxIndex:
        return TestSyntaxIndex(self.files, self.asts)

    @cached_property
    def contracts(self) -> ContractIndex:
        return ContractIndex(self.files)

    @cached_property
    def python_targets(self) -> PythonTargetIndex:
        return PythonTargetIndex(self.files)

    @cached_property
    def ordering(self) -> DefinitionOrderIndex:
        return DefinitionOrderIndex(
            self.files, self.contracts, self.asts, self.python_targets
        )

    def ensure(self, indexes: frozenset[Index]) -> None:
        """Build the requested indexes up front so failures surface before rules run."""
        for index in sorted(indexes):
            getattr(self, index.value)
