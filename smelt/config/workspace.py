"""uv workspace members and the member configs that refine the root policy.

A root ``smelt.yml`` with ``project.workspace: uv`` applies its source and test
roots inside every member that ``tool.uv.workspace`` declares. A member's own
``smelt.yml`` inherits the root implicitly and narrows rules, source conventions,
ignores and exclusions to that member's files.
"""

from __future__ import annotations

import posixpath
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from smelt.config.discovery import packages_in
from smelt.config.errors import ConfigError, ConfigIssue
from smelt.config.models import (
    IgnoreEntry,
    PackageContract,
    PackagePolicy,
    SourceExclusion,
)
from smelt.config.source import SourceStore

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.config.models import MemberConfig, SmeltConfig

_MEMBER_CONFIG_NAMES = ("smelt.yml", "smelt.yaml")
_UNSUPPORTED_MEMBER_KEYS = {
    "extends": "a member config inherits the workspace root implicitly",
    "architecture": "architecture is configured in the workspace root for now",
    "tests": "test options are configured in the workspace root",
    "suppressions": "the suppression policy is configured in the workspace root",
    "debt": "the debt file is configured in the workspace root",
    "members": "members are declared in pyproject.toml",
}


class WorkspaceError(ValueError):
    """``pyproject.toml`` declares the uv workspace in an unreadable shape."""


@dataclass(frozen=True, slots=True)
class MemberDirectory:
    """A directory a ``tool.uv.workspace.members`` glob matched, or why none."""

    path: str  # POSIX, relative to the workspace root
    skipped: str | None = None


def member_config_files(
    root: Path, *, store: SourceStore | None = None
) -> dict[str, Path]:
    """The ``smelt.yml`` of every declared member that has one, by member path."""
    store = store or SourceStore()
    found: dict[str, Path] = {}
    for member in member_directories(root, store=store):
        if member.skipped is not None:
            continue
        for name in _MEMBER_CONFIG_NAMES:
            candidate = root / member.path / name
            if store.is_file(candidate):
                found[member.path] = candidate
                break
    return found


def member_raw_issues(raw: object) -> list[ConfigIssue]:
    """Keys a member file may not set, with the reason; the model checks the rest."""
    if not isinstance(raw, dict):
        return [ConfigIssue("", "the top level must be a mapping")]
    issues = [
        ConfigIssue(key, f"not allowed in a workspace member config: {reason}")
        for key, reason in _UNSUPPORTED_MEMBER_KEYS.items()
        if key in raw
    ]
    project = raw.get("project")
    if isinstance(project, dict):
        issues.extend(
            ConfigIssue(
                f"project.{key}",
                "not allowed in a workspace member config: the workspace root "
                "selects roots and packages; a member may only add `exclude`",
            )
            for key in project
            if key != "exclude"
        )
    return issues


def expand_workspace(
    config: SmeltConfig, root: Path, *, store: SourceStore | None = None
) -> SmeltConfig:
    """Apply the roots inside every member, then compile the member configs."""
    store = store or SourceStore()
    try:
        members = [
            member.path
            for member in member_directories(root, strict=True, store=store)
            if member.skipped is None
        ]
    except WorkspaceError as exc:
        raise ConfigError([ConfigIssue("project.workspace", str(exc))]) from exc
    unknown = sorted(set(config.members) - set(members))
    if unknown:
        raise ConfigError(
            [
                ConfigIssue(f"members.{path}", "not a declared uv workspace member")
                for path in unknown
            ]
        )
    project = config.project
    bases = ([""] if _declares_project(root, store=store) else []) + members
    if "source_roots" in project.model_fields_set:
        sources = _existing(root, bases, project.source_roots, store=store)
    else:
        sources = [_default_root(root, base, store=store) for base in bases]
    if not sources:
        where = ", ".join(project.source_roots)
        raise ConfigError(
            [
                ConfigIssue(
                    "project.source_roots",
                    f"no workspace member contains a source root ({where})",
                )
            ]
        )
    # Workspace-level integration tests are common even when the root is no project.
    tests = _existing(root, ["", *members], project.test_roots, store=store)
    packages = _member_packages(root, members, sources, store=store)
    explicit = set(project.root_packages)
    if explicit:
        packages = {
            member: [name for name in names if name in explicit]
            for member, names in packages.items()
        }
    root_packages = project.root_packages or list(
        dict.fromkeys(name for names in packages.values() for name in names)
    )
    expanded = config.model_copy(
        update={
            "project": project.model_copy(
                update={
                    "source_roots": sources,
                    "test_roots": tests,
                    "root_packages": root_packages,
                }
            )
        }
    )
    return _compile_members(expanded, packages)


def member_directories(
    root: Path, *, strict: bool = False, store: SourceStore | None = None
) -> list[MemberDirectory]:
    """Every member ``tool.uv.workspace`` declares, sorted by path."""
    store = store or SourceStore()
    workspace = _pyproject(root, strict=strict, store=store)
    for key in ("tool", "uv", "workspace"):
        value = workspace.get(key, {})
        if not isinstance(value, dict):
            if strict:
                raise WorkspaceError(
                    f"pyproject.toml: {key} must be a table for workspace discovery"
                )
            return []
        workspace = value
    patterns = _strings(
        workspace.get("members"), strict=strict, field="tool.uv.workspace.members"
    )
    excluded = _strings(
        workspace.get("exclude"), strict=strict, field="tool.uv.workspace.exclude"
    )
    resolved_root = store.resolve(root)
    found: dict[str, MemberDirectory] = {}
    for pattern in patterns:
        matches = [
            candidate
            for candidate in store.glob(root, pattern)
            if store.is_dir(candidate)
            and store.resolve(candidate).is_relative_to(resolved_root)
            and store.resolve(candidate) != resolved_root
        ]
        if not matches:
            found.setdefault(
                pattern, MemberDirectory(pattern, skipped="matches no directory")
            )
        for candidate in matches:
            relative = candidate.relative_to(root).as_posix()
            if any(candidate in store.glob(root, entry) for entry in excluded):
                reason = "excluded by tool.uv.workspace.exclude"
                found[relative] = MemberDirectory(relative, skipped=reason)
            else:
                found[relative] = MemberDirectory(relative)
    return [found[key] for key in sorted(found)]


def _declares_project(root: Path, *, store: SourceStore) -> bool:
    """uv treats a root with a ``[project]`` table as a member of its own workspace."""
    return isinstance(_pyproject(root, store=store).get("project"), dict)


def _default_root(root: Path, base: str, *, store: SourceStore) -> str:
    """``src/`` when it holds a package, else the member directory itself."""
    src = posixpath.join(base, "src") if base else "src"
    return src if packages_in(root / src, store=store) else base or "."


def _existing(
    root: Path, bases: list[str], roots: list[str], *, store: SourceStore
) -> list[str]:
    joined = (
        posixpath.normpath(posixpath.join(base, entry))
        for base in bases
        for entry in roots
    )
    return list(dict.fromkeys(p for p in joined if store.is_dir(root / p)))


def _member_packages(
    root: Path, members: list[str], sources: list[str], *, store: SourceStore
) -> dict[str, list[str]]:
    """Top-level package names per member; ``""`` holds the root's own packages."""
    owners: dict[str, str] = {}
    packages: dict[str, list[str]] = {}
    member_dirs = {store.resolve(root / path) for path in members}
    for source in sources:
        member = _owner(source, members)
        for package in packages_in(root / source, store=store):
            resolved = store.resolve(package)
            if member == "" and any(
                resolved == directory or resolved.is_relative_to(directory)
                for directory in member_dirs
            ):
                continue
            previous = owners.setdefault(package.name, member)
            if previous != member:
                raise ConfigError(
                    [
                        ConfigIssue(
                            "project.workspace",
                            f'package "{package.name}" exists in workspace members '
                            f"{previous or '.'} and {member or '.'}; top-level "
                            "package names must be unique",
                        )
                    ]
                )
            packages.setdefault(member, [])
            if package.name not in packages[member]:
                packages[member].append(package.name)
    return packages


def _owner(path: str, members: list[str]) -> str:
    """The longest member path containing ``path``; ``""`` for the root itself."""
    owners = [m for m in members if path == m or path.startswith(m + "/")]
    return max(owners, key=len) if owners else ""


def _compile_members(
    config: SmeltConfig, packages: dict[str, list[str]]
) -> SmeltConfig:
    """Turn member structure, ignores and exclusions into root-level equivalents.

    Member rules stay in ``config.members``: they apply by path, so they also
    reach the member's tests. Structure options become one implicit contract per
    member package, so a member contract or a root contract closer to a module
    still overrides them.
    """
    contracts = dict(config.structure.contracts)
    ignore = list(config.ignore)
    exclude = list(config.project.exclude)
    issues: list[ConfigIssue] = []
    for path, member in config.members.items():
        names = packages.get(path, [])
        location = f"members.{path}"
        options = _policy_options(member)
        if options:
            for package in names:
                name = path if len(names) == 1 else f"{path}:{package}"
                contracts[name] = PackageContract(match=package, **options)
        for name, contract in member.structure.contracts.items():
            qualified = f"{path}:{name}"
            if contract.match.split(".")[0] not in names:
                issues.append(
                    ConfigIssue(
                        f"{location}.structure.contracts.{name}.match",
                        f'"{contract.match}" is outside this member; its packages '
                        f"are {', '.join(names) or 'none'}",
                    )
                )
            if qualified in contracts:
                issues.append(
                    ConfigIssue(
                        f"{location}.structure.contracts.{name}",
                        f'contract name "{qualified}" is already used',
                    )
                )
            contracts[qualified] = contract
        ignore.extend(_member_ignore(path, entry) for entry in member.ignore)
        exclude.extend(
            _member_exclusion(path, entry) for entry in member.project.exclude
        )
    if issues:
        raise ConfigError(issues)
    return config.model_copy(
        update={
            "structure": config.structure.model_copy(update={"contracts": contracts}),
            "ignore": ignore,
            "project": config.project.model_copy(update={"exclude": exclude}),
        }
    )


def _policy_options(member: MemberConfig) -> dict[str, Any]:
    structure = member.structure
    return {
        name: getattr(structure, name)
        for name in PackagePolicy.model_fields
        if getattr(structure, name) is not None
    }


def _member_ignore(path: str, entry: IgnoreEntry) -> IgnoreEntry:
    return entry.model_copy(
        update={"paths": [posixpath.join(path, item) for item in entry.paths]}
    )


def _member_exclusion(path: str, entry: str | SourceExclusion) -> str | SourceExclusion:
    if isinstance(entry, str):
        return posixpath.join(path, entry)
    return entry.model_copy(update={"path": posixpath.join(path, entry.path)})


def _pyproject(
    directory: Path, *, strict: bool = False, store: SourceStore
) -> dict[str, Any]:
    try:
        text = store.read_text(directory / "pyproject.toml")
        return tomllib.loads(text)
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        if strict:
            raise WorkspaceError(
                f"cannot discover workspace from pyproject.toml: {exc}"
            ) from exc
        return {}


def _strings(
    value: object, *, strict: bool = False, field: str = "workspace"
) -> list[str]:
    if (
        strict
        and value is not None
        and (
            not isinstance(value, list)
            or not all(isinstance(item, str) for item in value)
        )
    ):
        raise WorkspaceError(f"pyproject.toml: {field} must be a list of strings")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
