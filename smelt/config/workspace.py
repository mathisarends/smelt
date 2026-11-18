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
    ArchitectureConfig,
    IgnoreEntry,
    PackageContract,
    PackagePolicy,
    SourceExclusion,
)
from smelt.config.source import SourceStore

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from smelt.config.models import SmeltConfig

_MEMBER_CONFIG_NAMES = ("smelt.yml", "smelt.yaml")
_UNSUPPORTED_MEMBER_KEYS = {
    "extends": "a member config inherits the workspace root implicitly",
    "tests": "test options are configured in the workspace root",
    "suppressions": "the suppression policy is configured in the workspace root",
    "members": "members are declared in pyproject.toml",
}


class WorkspaceError(ValueError):
    """``pyproject.toml`` declares the uv workspace in an unreadable shape."""


@dataclass(frozen=True, slots=True)
class MemberDirectory:
    """A directory a ``tool.uv.workspace.members`` glob matched, or why none."""

    path: str  # POSIX, relative to the workspace root
    skipped: str | None = None


@dataclass(frozen=True, slots=True)
class ArchitectureScope:
    """The packages one architecture governs: the root's, or a member's own.

    ``config`` is the root config with this scope's architecture, so rules that
    read ``config.architecture`` need no knowledge of workspaces.
    """

    member: str | None  # None for the root architecture
    config: SmeltConfig
    packages: tuple[str, ...]

    @property
    def location(self) -> str:
        """Where this scope's architecture is declared, as a config key."""
        if self.member is None:
            return "architecture"
        return f"members.{self.member}.architecture"


@dataclass(frozen=True, slots=True)
class DebtScope:
    """A debt file and the member whose findings it may accept."""

    path: str
    member: str | None = None


def debt_scopes(config: SmeltConfig) -> tuple[DebtScope, ...]:
    members = tuple(
        DebtScope(posixpath.join(path, member.debt), path)
        for path, member in sorted(
            config.members.items(), key=lambda item: -len(item[0])
        )
        if member.debt is not None
    )
    return (*members, *((DebtScope(config.debt),) if config.debt else ()))


def debt_scope_for_path(
    scopes: tuple[DebtScope, ...], path: str | None
) -> DebtScope | None:
    """The closest member file wins over root debt; root debt covers the remainder."""
    return next(
        (
            scope
            for scope in scopes
            if scope.member is None
            or (
                path is not None
                and (path == scope.member or path.startswith(scope.member + "/"))
            )
        ),
        None,
    )


def architecture_scopes(config: SmeltConfig) -> tuple[ArchitectureScope, ...]:
    """Independent architectures for the root project and every workspace member."""
    if not config.workspace_packages:
        return ()
    return (
        ArchitectureScope(None, config, config.workspace_packages.get("", ())),
        *(
            ArchitectureScope(path, _member_architecture(config, path), packages)
            for path, packages in sorted(config.workspace_packages.items())
            if path
        ),
    )


def scope_for_path(
    scopes: tuple[ArchitectureScope, ...], path: str | None
) -> ArchitectureScope:
    """The innermost member scope containing ``path``; else the root scope."""
    owners = [
        scope
        for scope in scopes
        if scope.member is not None
        and path is not None
        and (path == scope.member or path.startswith(scope.member + "/"))
    ]
    if owners:
        return max(owners, key=lambda scope: len(scope.member or ""))
    return scopes[0]


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
    architecture = raw.get("architecture")
    if isinstance(architecture, dict):
        issues.extend(
            ConfigIssue(
                f"architecture.{key}",
                "not allowed in a workspace member config: this policy "
                "is set once in the workspace root",
            )
            for key in ("import_graph", "members", "canonical_exports")
            if key in architecture
        )
    style = raw.get("style")
    if isinstance(style, dict):
        issues.extend(
            ConfigIssue(
                f"style.{key}",
                "not allowed in a workspace member config: this policy "
                "is set once in the workspace root",
            )
            for key in ("forbidden_names", "file_naming")
            if key in style
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
        sources = [
            _default_root(root, base, selected=project.root_packages, store=store)
            for base in bases
        ]
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
    packages = _member_packages(
        root, members, sources, selected=project.root_packages, store=store
    )
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
    expanded = expanded.with_workspace_packages(
        {"": packages.get("", []), **{path: packages.get(path, []) for path in members}}
    )
    base = expanded.model_copy(update={"members": {}})
    return _compile_members(expanded, packages).with_workspace_base(base)


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


def _member_architecture(config: SmeltConfig, path: str) -> SmeltConfig:
    member = config.members.get(path)
    local = (
        member.architecture if member and member.architecture else ArchitectureConfig()
    )
    architecture = local.model_copy(
        update={"import_graph": config.architecture.import_graph}
    )
    return config.model_copy(update={"architecture": architecture})


def _declares_project(root: Path, *, store: SourceStore) -> bool:
    """uv treats a root with a ``[project]`` table as a member of its own workspace."""
    return isinstance(_pyproject(root, store=store).get("project"), dict)


def _default_root(
    root: Path, base: str, *, selected: list[str], store: SourceStore
) -> str:
    """``src/`` when it holds a package, else the member directory itself."""
    src = posixpath.join(base, "src") if base else "src"
    if packages_in(root / src, store=store) or (
        selected and _package_directories(root / src, selected, store=store)
    ):
        return src
    return base or "."


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
    root: Path,
    members: list[str],
    sources: list[str],
    *,
    selected: list[str],
    store: SourceStore,
) -> dict[str, list[str]]:
    """Top-level package names per member; ``""`` holds the root's own packages."""
    owners: dict[str, str] = {}
    packages: dict[str, list[str]] = {}
    member_dirs = {store.resolve(root / path) for path in members}
    candidates = {
        source: _package_directories(root / source, selected, store=store)
        for source in sources
    }
    regular = {
        package
        for directories in candidates.values()
        for package in directories
        if selected and store.is_file(package / "__init__.py")
    }
    regular_names = {package.name for package in regular}
    for source, directories in candidates.items():
        member = _owner(source, members)
        for package in directories:
            # Explicit package selection follows Python's regular-package precedence.
            if package.name in regular_names and package not in regular:
                continue
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


def _package_directories(
    source: Path, selected: list[str], *, store: SourceStore
) -> list[Path]:
    """Honor explicit packages, including namespaces that discovery would omit."""
    if selected:
        return [source / name for name in selected if store.is_dir(source / name)]
    return packages_in(source, store=store)


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
    member package. More specific overrides belong to that member.
    """
    contracts = dict(config.structure.contracts)
    ignore = list(config.ignore)
    exclude = list(config.project.exclude)
    issues = [
        ConfigIssue(
            f"{section}.{name}.match",
            f'"{policy.match}" can match packages in {", ".join(owners)}; '
            "move the entry to the owning member config",
        )
        for section, entries in (
            ("architecture.packages", config.architecture.packages),
            ("style.overrides", config.style.overrides),
        )
        for name, policy in entries.items()
        if (
            owners := [
                path
                for path, names in sorted(packages.items())
                if path and names and policy.match.split(".")[0] in ("*", *names)
            ]
        )
    ]
    for path, member in config.members.items():
        names = packages.get(path, [])
        location = f"members.{path}"
        options = _policy_options(member.structure)
        issues.extend(
            _style_reason_issues(
                options,
                member.structure.reasons,
                f"{location}.style",
                inherited=config.style,
            )
        )
        if options:
            for package in names:
                name = path if len(names) == 1 else f"{path}:{package}"
                contracts[name] = PackageContract(
                    match=package, reasons=member.style.reasons, **options
                ).with_style_location(f"{location}.style")
        inherited_style = config.style.model_copy(update=options)
        for name, contract in member.structure.contracts.items():
            qualified = f"{path}:{name}"
            policy = (
                "style.overrides"
                if name in member.style.overrides
                else "architecture.packages"
            )
            contract_location = f"{location}.{policy}.{name}"
            if contract.match.split(".")[0] not in names:
                issues.append(
                    ConfigIssue(
                        f"{contract_location}.match",
                        f'"{contract.match}" is outside this member; its packages '
                        f"are {', '.join(names) or 'none'}",
                    )
                )
            if qualified in contracts:
                issues.append(
                    ConfigIssue(
                        contract_location,
                        f'contract name "{qualified}" is already used',
                    )
                )
            issues.extend(
                _style_reason_issues(
                    _policy_options(contract),
                    contract.reasons,
                    contract_location,
                    inherited=inherited_style,
                )
            )
            contracts[qualified] = (
                contract.with_style_location(contract_location, override=True)
                if contract.style_override
                else contract
            )
        ignore.extend(_member_ignore(path, entry) for entry in member.ignore)
        exclude.extend(
            _member_exclusion(path, entry) for entry in member.project.exclude
        )
    if issues:
        raise ConfigError(issues)
    compiled = config.model_copy(
        update={
            "ignore": ignore,
            "project": config.project.model_copy(update={"exclude": exclude}),
            "members": {
                path: member.with_packages(packages.get(path, []))
                for path, member in config.members.items()
            },
        }
    )
    return compiled.with_compiled_structure(
        config.structure.model_copy(update={"contracts": contracts})
    )


def _policy_options(policy: PackagePolicy) -> dict[str, Any]:
    return {
        name: getattr(policy, name)
        for name in PackagePolicy.model_fields
        if getattr(policy, name) is not None
    }


def _style_reason_issues(
    options: Mapping[str, Any],
    reasons: Mapping[str, str],
    location: str,
    *,
    inherited: PackagePolicy,
) -> list[ConfigIssue]:
    return [
        ConfigIssue(
            f"{location}.{name}",
            "changing the inherited value requires a reason; use {value: ..., reason: ...}",
        )
        for name, value in options.items()
        if name not in reasons
        and (other := getattr(inherited, name)) is not None
        and other != value
    ]


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
