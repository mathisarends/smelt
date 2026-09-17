from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property
from typing import TYPE_CHECKING

from smelt.analysis.files import FileIndex
from smelt.analysis.imports import ImportIndex
from smelt.analysis.parsing import AstCache
from smelt.analysis.roles import RoleIndex
from smelt.analysis.syntax import SyntaxIndex
from smelt.analysis.types import PyrightTypes, TypeIndex
from smelt.model import ArchitectureModel

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.config.models import SmeltConfig


class Index(StrEnum):
    FILES = "files"
    IMPORTS = "imports"
    SYNTAX = "syntax"
    ROLES = "roles"
    TYPES = "types"
    CHANGES = "changes"


@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    added: int
    deleted: int


@dataclass(frozen=True)
class ChangeSet:
    """Files changed against a git base; paths are relative to the project root."""

    files: dict[str, FileChange] = field(default_factory=dict)

    def __contains__(self, path: object) -> bool:
        return path in self.files

    @property
    def paths(self) -> frozenset[str]:
        return frozenset(self.files)


class AnalysisContext:
    def __init__(
        self,
        root: Path,
        config: SmeltConfig,
        *,
        changes: ChangeSet | None = None,
        types: TypeIndex | None = None,
    ) -> None:
        self.root = root
        self.config = config
        self.changes = changes
        self._types = types

    @cached_property
    def types(self) -> TypeIndex | None:
        """The configured type backend, or None when there is none to be had."""
        if self._types is not None:
            return self._types
        if self.config.analysis.types == "pyright":
            return PyrightTypes.discover(self.root, self.config)
        return None

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

    @cached_property
    def roles(self) -> RoleIndex:
        return RoleIndex(self.config, self.syntax, self.types)

    def ensure(self, indexes: frozenset[Index]) -> None:
        """Build the requested indexes up front so failures surface before rules run."""
        for index in sorted(indexes):
            if index is Index.TYPES or index is Index.CHANGES:
                continue
            getattr(self, index.value)
