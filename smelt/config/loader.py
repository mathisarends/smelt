from __future__ import annotations

import re
import types
import typing
from collections.abc import Hashable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ValidationError

from smelt.config.composition import ConfigComposition, add_member, compose_config
from smelt.config.discovery import packages_in
from smelt.config.errors import (
    UPGRADE_GUIDE_URL,
    ConfigError,
    ConfigIssue,
    did_you_mean,
    format_loc,
)
from smelt.config.models import LIMIT_RULES, SmeltConfig
from smelt.config.source import SourceStore
from smelt.config.validation import partial_layer_issues, validate_semantics
from smelt.config.workspace import (
    expand_workspace,
    member_config_files,
    member_directories,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from pathlib import Path

CONFIG_FILENAME = "smelt.yml"
CONFIG_FILENAMES = (CONFIG_FILENAME, "smelt.yaml")

_REMOVED_SWITCHES = {
    "member_visibility": "visibility/internal-public-member",
    "static_methods": "layout/misplaced-static-helper",
    "instance_assignments": "idioms/bundled-instance-assignment",
    "forbid_private_imports": "tests/private-access",
    "forbid_string_patch_targets": "tests/string-patch-target",
    "require_assertions": "tests/missing-outcome-check",
    "require_non_vacuous_assertions": "tests/vacuous-assertion",
    "review_test_deadlines": "tests/unbounded-wait",
    "review_case_loops": "tests/independent-case-loop",
    "review_async_mock_assertions": "tests/missing-await-assertion",
    "review_test_only_public_functions": "visibility/test-only-public-function",
    "review_test_only_public_attributes": "visibility/test-only-public-attribute",
}


class _StrictBoolLoader(yaml.SafeLoader):
    """YAML 1.2 booleans: only ``true``/``false``, so ``off`` stays a string."""

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        if isinstance(node, yaml.MappingNode):
            keys: dict[Hashable, yaml.Node] = {}
            for key_node, _ in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    continue  # Explicit keys may override inherited YAML defaults.
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, Hashable):
                    continue  # The standard constructor diagnoses invalid keys.
                if key in keys:
                    raise yaml.constructor.ConstructorError(
                        "first definition of key",
                        keys[key].start_mark,
                        f"duplicate key {key!r}",
                        key_node.start_mark,
                    )
                keys[key] = key_node
        return super().construct_mapping(node, deep=deep)


_StrictBoolLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictBoolLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def _load_yaml(text: str) -> object:
    return yaml.load(text, Loader=_StrictBoolLoader)  # noqa: S506


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    config: SmeltConfig
    path: Path
    warnings: tuple[ConfigIssue, ...] = ()
    source: SourceStore | None = None
    inputs: tuple[Path, ...] = ()
    origins: dict[str, str] = field(default_factory=dict)
    member: str | None = None  # the workspace member whose config was selected
    rule_sources: tuple[str, ...] = ()

    @property
    def root(self) -> Path:
        """Project root: the directory containing the config file."""
        return self.path.parent

    def explain_error(self, error: ConfigError) -> ConfigError:
        """Attribute effective-policy errors to the file declaring each value."""
        if len(self.inputs) <= 1:
            return error.relative_to(self.root)
        return _with_origins(
            error,
            ConfigComposition(
                raw={"members": self.config.members}, origins=self.origins
            ),
        ).relative_to(self.root)

    def source_for(self, location: str) -> str | None:
        """The nearest explicitly declared key's file."""
        return ConfigComposition(origins=self.origins).source_for(location)


def discover_config(start: Path, *, store: SourceStore | None = None) -> Path | None:
    """Walk up from ``start`` looking for ``smelt.yml`` or ``smelt.yaml``."""
    store = store or SourceStore()
    current = store.resolve(start)
    for directory in (current, *current.parents):
        for filename in CONFIG_FILENAMES:
            candidate = directory / filename
            if store.is_file(candidate):
                return candidate
    return None


def load_config(path: Path, *, store: SourceStore | None = None) -> LoadedConfig:
    """Load a project config; a workspace member's config selects its root."""
    view = store or SourceStore()
    path = view.resolve(path)
    member: str | None = None
    try:
        if (workspace := _workspace_of(path, view)) is not None:
            path, member = workspace
        return _load_selected(path, view, member=member, store=store)
    except ConfigError as exc:
        shown = exc.relative_to(path.parent)
        if shown is exc:
            raise
        raise shown from exc


def config_files(path: Path, *, store: SourceStore | None = None) -> tuple[Path, ...]:
    """Every file a load of ``path`` reads: the root config, its bases and member configs.

    Only the YAML is read, so a version 1 workspace can be migrated as a whole.
    """
    view = store or SourceStore()
    path = view.resolve(path)
    if (workspace := _workspace_of(path, view)) is not None:
        path = workspace[0]
    composed = compose_config(path, view, _load_yaml)
    files = list(composed.files)
    if _declares_workspace(composed.raw):
        files.extend(member_config_files(path.parent, store=view).values())
    return tuple(dict.fromkeys(files))


def parse_config(
    raw: object,
    *,
    source: str | None = None,
    root: Path | None = None,
    store: SourceStore | None = None,
) -> tuple[SmeltConfig, tuple[ConfigIssue, ...]]:
    """Validate a raw config; ``root`` lets omitted root_packages be discovered."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError([ConfigIssue("", "the top level must be a mapping")], source)
    if "version" not in raw:
        raise ConfigError([ConfigIssue("version", "`version: 2` is required")], source)
    if outdated := _outdated_versions(raw):
        raise ConfigError(outdated, source)
    try:
        config = SmeltConfig.model_validate(raw)
    except ValidationError as exc:
        issues = _issues_from_validation(exc)
        reported = {issue.path for issue in issues}
        for issue in partial_layer_issues(raw):
            if issue.path not in reported:
                issues.append(replace(issue, semantic=True))
                reported.add(issue.path)
        raise ConfigError(issues, source) from exc
    if config.members and config.project.workspace is None:
        raise ConfigError(
            [ConfigIssue("members", "member configs require project.workspace: uv")],
            source,
        )
    if config.project.workspace is not None and root is not None:
        try:
            config = expand_workspace(config, root, store=store)
        except ConfigError as exc:
            raise ConfigError(exc.issues, source) from exc
    config = _with_root_packages(config, root, source, store=store)
    errors, warnings = validate_semantics(config)
    if errors:
        raise ConfigError(errors, source)
    return config, tuple(warnings)


def raw_config(path: Path, *, store: SourceStore | None = None) -> Mapping[str, object]:
    """The composed YAML before validation; empty when it cannot be read."""
    try:
        raw = compose_config(path, store or SourceStore(), _load_yaml).raw
    except ConfigError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _outdated_versions(raw: Mapping[str, object]) -> list[ConfigIssue]:
    """Version 1 files are not read; the migration converts them."""
    members = raw.get("members")
    files = [("", raw)] + (
        [
            (f"members.{name}.", member)
            for name, member in members.items()
            if isinstance(member, dict)
        ]
        if isinstance(members, dict)
        else []
    )
    return [
        ConfigIssue(f"{prefix}version", _VERSION_1)
        for prefix, file in files
        if file.get("version") == 1
    ]


_VERSION_1 = (
    "version 1 is no longer read; run `smelt config migrate --write` "
    "to convert this file to version 2"
)


def _load_selected(
    path: Path, view: SourceStore, *, member: str | None, store: SourceStore | None
) -> LoadedConfig:
    composed = compose_config(path, view, _load_yaml)
    if "members" in composed.raw:
        raise ConfigError(
            [
                ConfigIssue(
                    "members",
                    "not a config key; each workspace member keeps its own smelt.yml",
                )
            ],
            composed.source_for("members") or path.name,
        )
    misplaced: tuple[ConfigIssue, ...] = ()
    if _declares_workspace(composed.raw):
        members = member_config_files(path.parent, store=view)
        for name, file in members.items():
            add_member(composed, name, file, store=view, parse_yaml=_load_yaml)
        misplaced = _misplaced_configs(path, view, known=set(composed.files))
    try:
        config, warnings = parse_config(
            composed.raw, source=path.name, root=path.parent, store=store
        )
    except ConfigError as exc:
        if composed.composite:
            raise _with_origins(exc, composed) from exc
        raise
    if len(composed.rule_layers) > 1:
        try:
            config = config.with_rule_layers(composed.rule_layers)
        except ValidationError as exc:
            raise _with_origins(
                ConfigError(_issues_from_validation(exc), path.name), composed
            ) from exc
    if composed.composite:
        warnings = tuple(
            ConfigIssue(issue.path, issue.message, composed.source_for(issue.path))
            for issue in warnings
        )
    return LoadedConfig(
        config=config,
        path=path,
        warnings=(*warnings, *misplaced),
        source=store,
        inputs=tuple(composed.files),
        origins=composed.origins,
        member=member,
        rule_sources=tuple(composed.rule_sources),
    )


def _workspace_of(path: Path, store: SourceStore) -> tuple[Path, str | None] | None:
    """Find the enclosing workspace even when a nested policy is misplaced."""
    if path.name not in CONFIG_FILENAMES:
        return None
    directory = path.parent
    for parent in directory.parents:
        candidate = next(
            (
                parent / name
                for name in CONFIG_FILENAMES
                if store.is_file(parent / name)
            ),
            None,
        )
        if candidate is None:
            continue
        try:
            composed = compose_config(candidate, store, _load_yaml)
        except ConfigError as exc:
            raise exc.relative_to(candidate.parent) from exc
        members = [
            entry.path
            for entry in member_directories(parent, store=store)
            if entry.skipped is None
        ]
        relative = directory.relative_to(parent).as_posix()
        if _declares_workspace(composed.raw):
            owners = [
                name
                for name in members
                if relative == name or relative.startswith(name + "/")
            ]
            return candidate, max(owners, key=len, default=None)
        if (
            relative in members
            and "version" not in compose_config(path, store, _load_yaml).raw
        ):
            raise ConfigError(
                [
                    ConfigIssue(
                        "project.workspace",
                        "this looks like a member config; set project.workspace: uv in the workspace root",
                    )
                ],
                candidate.as_posix(),
            )
        if relative not in members:
            continue
        return None
    return None


def _misplaced_configs(
    root_file: Path, store: SourceStore, *, known: set[Path]
) -> tuple[ConfigIssue, ...]:
    root = root_file.parent
    return tuple(
        ConfigIssue(
            "",
            "ignored config; workspace policy belongs at the root or in a declared member directory",
            candidate.relative_to(root).as_posix(),
        )
        for candidate in sorted(_workspace_config_paths(root, store))
        if candidate not in known
    )


def _workspace_config_paths(root: Path, store: SourceStore) -> Iterator[Path]:
    inventory = store.yaml_inventory(root)
    skip = {"venv", "env", "node_modules", "build", "dist", "__pycache__"}
    if inventory is not None:
        for relative in inventory:
            candidate = root / relative
            if candidate.name in CONFIG_FILENAMES and not any(
                part.startswith(".") or part in skip
                for part in relative.split("/")[:-1]
            ):
                yield candidate
        return
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in store.iterdir(directory):
            if entry.name in CONFIG_FILENAMES and store.is_file(entry):
                yield entry
            elif (
                not entry.name.startswith(".")
                and entry.name not in skip
                and store.is_dir(entry)
                and store.resolve(entry) == entry
            ):
                pending.append(entry)


def _declares_workspace(raw: dict[str, Any]) -> bool:
    project = raw.get("project")
    return isinstance(project, dict) and project.get("workspace") == "uv"


def _with_origins(error: ConfigError, composed: ConfigComposition) -> ConfigError:
    return ConfigError(
        [
            replace(
                issue,
                path=_member_relative(issue.path, composed),
                source=composed.source_for(issue.path),
            )
            for issue in error.issues
        ],
        error.source,
    )


def _member_relative(location: str, composed: ConfigComposition) -> str:
    """Name a member's key as written in its own file: ``rules.x``, not ``members.m.rules.x``."""
    prefixes = (f"members.{member}." for member in composed.raw.get("members", {}))
    return location.removeprefix(
        max(
            (prefix for prefix in prefixes if location.startswith(prefix)),
            key=len,
            default="",
        )
    )


def _with_root_packages(
    config: SmeltConfig,
    root: Path | None,
    source: str | None,
    *,
    store: SourceStore | None = None,
) -> SmeltConfig:
    project = config.project
    if project.root_packages:
        return config
    found = (
        list(
            dict.fromkeys(
                package.name
                for source_root in project.source_roots
                for package in packages_in(root / source_root, store=store)
            )
        )
        if root is not None
        else []
    )
    if not found:
        where = ", ".join(project.source_roots)
        raise ConfigError(
            [
                ConfigIssue(
                    "project.root_packages",
                    f"no packages found under source_roots ({where}); "
                    "set root_packages or source_roots",
                )
            ],
            source,
        )
    return config.model_copy(
        update={"project": project.model_copy(update={"root_packages": found})}
    )


def _issues_from_validation(exc: ValidationError) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    allowance_prefix = ("architecture", "cross_feature", "allow")
    branch_index = len(allowance_prefix) + 1
    exclusion_prefix = ("project", "exclude")
    exclusion_branch_index = len(exclusion_prefix) + 1
    for error in exc.errors():
        loc = _rule_setting_location(tuple(error["loc"]), value=error.get("input"))
        if loc is None:
            continue
        # Expose YAML keys, not the internal branches of the allowance union.
        if loc[: len(allowance_prefix)] == allowance_prefix and len(loc) > branch_index:
            branch = str(loc[branch_index])
            if branch == "str" and isinstance(error.get("input"), dict):
                continue
            if (
                len(loc) == branch_index + 1
                and branch.startswith("function-after")
                and isinstance(error.get("input"), str)
            ):
                continue
            loc = (*loc[:branch_index], *loc[branch_index + 1 :])
        elif (
            loc[: len(exclusion_prefix)] == exclusion_prefix
            and len(loc) > exclusion_branch_index
        ):
            branch = str(loc[exclusion_branch_index])
            if branch == "str" and isinstance(error.get("input"), dict):
                continue
            if (
                len(loc) == exclusion_branch_index + 1
                and branch == "SourceExclusion"
                and isinstance(error.get("input"), str)
            ):
                continue
            loc = (*loc[:exclusion_branch_index], *loc[exclusion_branch_index + 1 :])
        message = str(error["msg"]).removeprefix("Value error, ")
        if error["type"] == "extra_forbidden":
            key = str(loc[-1])
            known = _known_fields(SmeltConfig, loc[:-1])
            message = _unknown_key_message(key, known, loc)
        elif error["type"] == "missing":
            message = "required key is missing"
        elif error["type"] == "literal_error":
            message = f"{message}, got {error.get('input')!r}"
        issues.append(ConfigIssue(format_loc(loc), message))
    return issues


def _rule_setting_location(
    loc: tuple[str | int, ...], *, value: object
) -> tuple[str | int, ...] | None:
    """Show the supplied severity/object branch at the actual YAML key."""
    for index, branch in enumerate(loc):
        if index < 2 or loc[index - 2] != "rules":  # noqa: PLR2004 - map key and union branch follow rules
            continue
        if branch == "RuleOverride" or (
            str(branch).startswith("function-after[")
            and str(branch).endswith(", RuleOverride]")
        ):
            if len(loc) == index + 1 and not isinstance(value, dict):
                return None
            return (*loc[:index], *loc[index + 1 :])
        if str(branch).startswith("literal["):
            if isinstance(value, dict):
                return None
            return (*loc[:index], *loc[index + 1 :])
    return loc


def _unknown_key_message(key: str, known: list[str], loc: tuple[str | int, ...]) -> str:
    path = tuple(str(item) for item in loc[-2:])
    if retired := _RETIRED_POLICY_KEYS.get(path) or _RETIRED_POLICY_KEYS.get((key,)):
        return f"{retired}; see {UPGRADE_GUIDE_URL}"
    message = f'unknown key "{key}"{did_you_mean(key, known)}'
    if key in _REMOVED_SWITCHES:
        return (
            message
            + f"; this switch was removed: configure rules.{_REMOVED_SWITCHES[key]} with error, warning, hint or off instead"
        )
    if key == "enabled" and "ordering" in loc:
        return message + "; enable or disable rules.layout/definition-order instead"
    if key == "preset":
        return message + "; list rules individually or extend a local base file"
    return message


_RETIRED_POLICY_KEYS: dict[tuple[str, ...], str] = {
    ("structure",): "structure was replaced by style and architecture.packages",
    (
        "architecture",
        "imports",
    ): "architecture.imports was renamed to architecture.import_graph",
    (
        "style",
        "contracts",
    ): "use style.overrides for conventions and architecture.packages for package APIs",
    (
        "style",
        "canonical_exports",
    ): "canonical_exports moved to architecture.canonical_exports",
    ("bundle_imports_at",): "bundle_imports_at was renamed to min_imports_to_bundle",
    ("export_at",): "export_at was renamed to min_importers_to_export",
    (
        "module_import_exempt",
    ): "module_import_exempt was renamed to module_import_exemptions",
    ("unmirrored",): "unmirrored was renamed to mirror_exclude",
    ("forbid_mocks_in",): "forbid_mocks_in was renamed to forbidden_mock_paths",
    (
        "project",
        "coverage",
    ): "project.coverage was replaced by the rule; set rules.architecture/uncovered-source to error (smelt config migrate does this)",
    **{
        (option,): f"{option} moved into the rule entry; write "
        f"rules: {{{name}: {{severity: ..., max: ...}}}} in the same scope "
        "(smelt config migrate does this)"
        for option, (_, name) in LIMIT_RULES.items()
    },
}


def _known_fields(model: type[BaseModel], loc: Sequence[str | int]) -> list[str]:
    current: Any = model
    for item in loc:
        current = _step(current, item)
        if current is None:
            return []
    if isinstance(current, type) and issubclass(current, BaseModel):
        return [field.alias or name for name, field in current.model_fields.items()]
    return []


def _step(annotation: Any, item: str | int) -> Any:
    annotation = _unwrap_optional(annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        field = annotation.model_fields.get(str(item))
        return field.annotation if field else None
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is list and args:
        return args[0]
    if origin is dict and len(args) == 2:  # noqa: PLR2004
        return args[1]
    return None


def _unwrap_optional(annotation: Any) -> Any:
    origin = typing.get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation
