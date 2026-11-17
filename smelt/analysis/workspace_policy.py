"""Workspace ownership and declared dependencies, read through the captured store."""

from __future__ import annotations

import tomllib
from functools import cached_property
from typing import TYPE_CHECKING, Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from smelt.analysis.errors import AnalysisError
from smelt.config.workspace import member_directories

if TYPE_CHECKING:
    from smelt.analysis.files import FileIndex


class WorkspaceIndex:
    def __init__(self, files: FileIndex) -> None:
        self._files = files
        self._store = files.store
        self._enabled = files.config.project.workspace == "uv"
        self._paths = (
            tuple(
                member.path
                for member in member_directories(files.root, store=files.store)
                if member.skipped is None
            )
            if self._enabled
            else ()
        )

    @property
    def members(self) -> tuple[str, ...]:
        """Every analyzed member path, '.' (the workspace root) first."""
        return (".", *self._paths) if self._enabled else ()

    def member_for_module(self, module: str) -> str | None:
        source = self._files.sources.get(module)
        if source is not None:
            return self.member_for_path(source.path)
        owners = {
            self.member_for_path(part.path)
            for part in self._files.package_parts.get(module, [])
        }
        return next(iter(owners)) if len(owners) == 1 else None

    def member_for_path(self, path: str | None) -> str | None:
        """The innermost declared member, '.' for root files, None outside a workspace."""
        if not self._enabled or path is None:
            return None
        owners = [m for m in self._paths if path == m or path.startswith(m + "/")]
        return max(owners, key=len) if owners else "."

    @cached_property
    def metadata(self) -> dict[str, dict[str, Any]]:
        """All pyproject inputs, including absent files, for analysis cache identity."""
        if not self._enabled:
            return {}
        return {path: self._read_project(path) for path in (".", *self._paths)}

    @cached_property
    def dependencies(self) -> dict[str, frozenset[str]]:
        """Declared direct workspace dependencies; unknown/dynamic projects are omitted.

        Markers and extras do not depend on the analyzer's environment. Every
        declared project or optional dependency counts; dev groups do not.
        A uv source alone never declares a dependency.
        """
        names = self._project_names()
        root_sources = _sources(self.metadata.get(".", {}))
        declared: dict[str, frozenset[str]] = {}
        for path, data in self.metadata.items():
            project = data.get("project")
            if not isinstance(project, dict):
                continue
            dependencies = _requirements(project, path)
            if dependencies is None:
                continue
            local_sources = _sources(data)
            sources = {**root_sources, **local_sources}
            targets: set[str] = set()
            for dependency in dependencies:
                try:
                    name = canonicalize_name(Requirement(dependency).name)
                except InvalidRequirement as exc:
                    raise AnalysisError(
                        f"{path}/pyproject.toml: invalid dependency {dependency!r}: {exc}"
                    ) from exc
                if name not in names:
                    continue
                member = names[name]
                if name not in sources:
                    targets.add(member)
                for entry in _source_entries(sources.get(name)):
                    if entry.get("workspace") is True:
                        targets.add(member)
                    target = entry.get("path")
                    if isinstance(target, str):
                        base = path if name in local_sources else "."
                        directory = self._store.resolve(
                            self._files.root / base / target
                        )
                        if self._store.resolve(self._files.root / member) == directory:
                            targets.add(member)
            declared[path] = frozenset(targets)
        return declared

    def _project_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        for path, data in self.metadata.items():
            project = data.get("project", {})
            name = project.get("name") if isinstance(project, dict) else None
            if isinstance(name, str):
                normalized = canonicalize_name(name)
                if normalized in names:
                    raise AnalysisError(
                        f'duplicate workspace project name "{name}" in {names[normalized]} and {path}'
                    )
                names[normalized] = path
        return names

    def _read_project(self, member: str) -> dict[str, Any]:
        path = self._files.root / member / "pyproject.toml"
        try:
            return tomllib.loads(self._store.read_text(path))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise AnalysisError(
                f"{member}/pyproject.toml: cannot read workspace metadata: {exc}"
            ) from exc


def _sources(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("tool", "uv", "sources"):
        value = data.get(key, {})
        if not isinstance(value, dict):
            return {}
        data = value
    return {canonicalize_name(name): value for name, value in data.items()}


def _requirements(project: dict[str, Any], path: str) -> list[str] | None:
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list):
        raise AnalysisError(f"{path}/pyproject.toml: project.dynamic must be a list")
    if any(field in dynamic for field in ("dependencies", "optional-dependencies")):
        return None
    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, dict):
        raise AnalysisError(
            f"{path}/pyproject.toml: project.optional-dependencies must be a table"
        )
    groups = {
        "dependencies": project.get("dependencies", []),
        **{
            f"optional-dependencies.{extra}": values
            for extra, values in optional.items()
        },
    }
    requirements: list[str] = []
    for field, values in groups.items():
        if not isinstance(values, list) or not all(
            isinstance(value, str) for value in values
        ):
            raise AnalysisError(
                f"{path}/pyproject.toml: project.{field} must be a list of requirements"
            )
        requirements.extend(values)
    return requirements


def _source_entries(value: object) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    return []
