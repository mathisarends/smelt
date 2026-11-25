from importlib import import_module
from typing import TYPE_CHECKING, Any

from smelt.analysis.errors import AnalysisError

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext, Index
    from smelt.analysis.files import FileIndex, SourceFile, TestFile


__all__ = [
    "AnalysisContext",
    "AnalysisError",
    "FileIndex",
    "Index",
    "SourceFile",
    "TestFile",
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
}
