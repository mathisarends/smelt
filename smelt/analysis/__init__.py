from importlib import import_module
from typing import TYPE_CHECKING, Any

from smelt.analysis.errors import AnalysisError

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext, Index
    from smelt.analysis.files import FileIndex, SourceFile, TestFile
    from smelt.analysis.imports import ImportDetail, ImportIndex, is_stdlib
    from smelt.analysis.syntax import ModuleSyntax, SyntaxIndex


__all__ = [
    "AnalysisContext",
    "AnalysisError",
    "FileIndex",
    "ImportDetail",
    "ImportIndex",
    "Index",
    "ModuleSyntax",
    "SourceFile",
    "SyntaxIndex",
    "TestFile",
    "is_stdlib",
]


def __getattr__(name: str) -> Any:
    if name not in _MODULES:
        raise AttributeError(name)
    return getattr(import_module(f"smelt.analysis.{_MODULES[name]}"), name)


_MODULES = {
    "AnalysisContext": "context",
    "Index": "context",
    "FileIndex": "files",
    "SourceFile": "files",
    "TestFile": "files",
    "ImportDetail": "imports",
    "ImportIndex": "imports",
    "is_stdlib": "imports",
    "ModuleSyntax": "syntax",
    "SyntaxIndex": "syntax",
}
