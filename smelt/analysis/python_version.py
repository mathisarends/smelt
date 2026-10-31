"""Declared minimum Python versions, with per-project provenance and no execution."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.analysis.files import FileIndex

_DEFERRED_ANNOTATIONS = (3, 14)


@dataclass(frozen=True)
class PythonTarget:
    minimum: tuple[int, int] | None
    source: str | None
    requires_python: str | None = None
    uncertainty: str | None = None

    @property
    def deferred_annotations(self) -> bool:
        return (
            self.minimum is not None
            and self.minimum[0] == _DEFERRED_ANNOTATIONS[0]
            and self.minimum >= _DEFERRED_ANNOTATIONS
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "minimum": ".".join(map(str, self.minimum)) if self.minimum else None,
            "source": self.source,
            "requires_python": self.requires_python,
            "uncertainty": self.uncertainty,
        }


class PythonTargetIndex:
    def __init__(self, files: FileIndex) -> None:
        self.files = files
        self._cache: dict[Path, PythonTarget] = {}

    def for_path(self, path: Path | None = None) -> PythonTarget:
        override = self.files.config.project.python_version
        if override is not None:
            major, minor = override.split(".")
            return PythonTarget((int(major), int(minor)), "project.python_version")
        directory = (
            (path.parent if path.suffix == ".py" else path)
            if path is not None
            else self.files.root
        )
        directory = self.files.resolve_path(directory)
        boundary = self.files.root
        if not directory.is_relative_to(self.files.root):
            source_roots = [
                self.files.resolve_path(self.files.root / entry)
                for entry in self.files.config.project.source_roots
                if directory.is_relative_to(
                    self.files.resolve_path(self.files.root / entry)
                )
            ]
            if not source_roots:
                return PythonTarget(
                    None, None, uncertainty="path is outside the declared source roots"
                )
            boundary = max(source_roots, key=lambda entry: len(entry.parts)).parent
        if directory in self._cache:
            return self._cache[directory]
        visited: list[Path] = []
        target: PythonTarget | None = None
        for current in (directory, *directory.parents):
            if not current.is_relative_to(boundary):
                break
            visited.append(current)
            metadata = current / "pyproject.toml"
            if current in self._cache:
                target = self._cache[current]
                break
            if self.files.store.is_file(metadata):
                target = self._read(metadata)
                if target is not None:
                    break
        else:
            target = None
        result = target or PythonTarget(
            None, None, uncertainty="no declared minimum Python version"
        )
        for current in visited:
            self._cache[current] = result
        return result

    def describe(self) -> list[dict[str, Any]]:
        targets = {
            self.for_path(source.absolute) for source in self.files.sources.values()
        }
        return [
            target.to_json()
            for target in sorted(
                targets, key=lambda item: (item.source or "", item.minimum or (0, 0))
            )
        ]

    def _read(self, path: Path) -> PythonTarget | None:
        source = self.files.relative(path)
        try:
            data = tomllib.loads(self.files.store.read_text(path))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            return PythonTarget(
                None, source, uncertainty=f"cannot read Python metadata: {exc}"
            )
        project = data.get("project")
        if project is None:
            return None
        if not isinstance(project, dict):
            return PythonTarget(
                None, source, uncertainty="project metadata is not a table"
            )
        spec = project.get("requires-python")
        if not isinstance(spec, str):
            return PythonTarget(
                None, source, uncertainty="project has no string requires-python"
            )
        return _minimum_from_requires_python(spec, source)


def _minimum_from_requires_python(spec: str, source: str) -> PythonTarget:
    """A conservative lower bound. Exclusions never raise the inferred minimum."""
    bounds: list[Version] = []
    try:
        for item in SpecifierSet(spec):
            if item.operator in {">=", ">", "~=", "=="}:
                version = Version(item.version.removesuffix(".*"))
                if version.is_prerelease or version.is_devrelease:
                    return PythonTarget(
                        None,
                        source,
                        spec,
                        "prerelease minimum is not a stable Python target",
                    )
                bounds.append(version)
            elif item.operator == "===":
                return PythonTarget(
                    None,
                    source,
                    spec,
                    "arbitrary version equality has no inferred Python minimum",
                )
    except (InvalidSpecifier, InvalidVersion) as exc:
        return PythonTarget(None, source, spec, f"invalid requires-python: {exc}")
    if not bounds:
        return PythonTarget(
            None, source, spec, "requires-python has no explicit lower bound"
        )
    release = max(bounds).release
    return PythonTarget(
        (release[0], release[1] if len(release) > 1 else 0), source, spec
    )
