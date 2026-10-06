from __future__ import annotations

from enum import StrEnum
from functools import cached_property
from typing import TYPE_CHECKING

from smelt.analysis.files import FileIndex
from smelt.analysis.imports import ImportIndex
from smelt.analysis.parsing import AstCache
from smelt.analysis.syntax import SyntaxIndex
from smelt.model import ArchitectureModel

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.config.models import SmeltConfig


class Index(StrEnum):
    FILES = "files"
    IMPORTS = "imports"
    SYNTAX = "syntax"


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
    def imports(self) -> ImportIndex:
        return ImportIndex.build(self.files, self.asts)

    @cached_property
    def syntax(self) -> SyntaxIndex:
        return SyntaxIndex(self.files, self.asts)

    def ensure(self, indexes: frozenset[Index]) -> None:
        """Build the requested indexes up front so failures surface before rules run."""
        for index in sorted(indexes):
            getattr(self, index.value)
