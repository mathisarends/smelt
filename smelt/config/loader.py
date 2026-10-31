from __future__ import annotations

import re
import types
import typing
from collections.abc import Hashable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ValidationError

from smelt.config.discovery import packages_in
from smelt.config.errors import ConfigError, ConfigIssue, did_you_mean, format_loc
from smelt.config.models import SmeltConfig
from smelt.config.source import SourceStore
from smelt.config.validation import validate_semantics

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

CONFIG_FILENAME = "smelt.yaml"
CONFIG_FILENAMES = (CONFIG_FILENAME, "smelt.yml")

_REMOVED_SWITCHES = {
    "member_visibility": "internal-public-member",
    "static_methods": "internal-static-helper",
    "instance_assignments": "bundled-instance-assignment",
    "forbid_private_imports": "private-test-access",
    "forbid_string_patch_targets": "string-patch-target",
    "require_assertions": "test-without-outcome-check",
    "require_non_vacuous_assertions": "test-only-checks-possibly-empty-data",
    "review_test_deadlines": "unbounded-test-wait",
    "review_case_loops": "independent-test-case-loop",
    "review_async_mock_assertions": "async-mock-without-await-check",
    "review_test_only_public_functions": "test-only-public-function",
    "review_test_only_public_attributes": "test-only-public-attribute",
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

    @property
    def root(self) -> Path:
        """Project root: the directory containing the config file."""
        return self.path.parent


def discover_config(start: Path, *, store: SourceStore | None = None) -> Path | None:
    """Walk up from ``start`` looking for ``smelt.yaml`` or ``smelt.yml``."""
    store = store or SourceStore()
    current = store.resolve(start)
    for directory in (current, *current.parents):
        for filename in CONFIG_FILENAMES:
            candidate = directory / filename
            if store.is_file(candidate):
                return candidate
    return None


def load_config(path: Path, *, store: SourceStore | None = None) -> LoadedConfig:
    source = path.name
    try:
        raw = _load_yaml((store or SourceStore()).read_text(path))
    except (OSError, UnicodeError) as exc:
        raise ConfigError([ConfigIssue("", f"cannot read {path}: {exc}")]) from exc
    except yaml.YAMLError as exc:
        raise ConfigError([ConfigIssue("", f"invalid YAML: {exc}")], source) from exc
    config, warnings = parse_config(raw, source=source, root=path.parent, store=store)
    return LoadedConfig(
        config=config,
        path=(store or SourceStore()).resolve(path),
        warnings=warnings,
        source=store,
    )


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
        raise ConfigError([ConfigIssue("version", "`version: 1` is required")], source)
    try:
        config = SmeltConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_issues_from_validation(exc), source) from exc
    config = _with_root_packages(config, root, source, store=store)
    errors, warnings = validate_semantics(config)
    if errors:
        raise ConfigError(errors, source)
    return config, tuple(warnings)


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
        loc = tuple(error["loc"])
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


def _unknown_key_message(key: str, known: list[str], loc: tuple[str | int, ...]) -> str:
    message = f'unknown key "{key}"{did_you_mean(key, known)}'
    if key in _REMOVED_SWITCHES:
        return (
            message
            + f"; this switch was removed: configure rules.{_REMOVED_SWITCHES[key]} with error, warning, hint or off instead"
        )
    if key == "enabled" and "ordering" in loc:
        return message + "; enable or disable rules.public-definitions-first instead"
    if key == "preset":
        return (
            message
            + "; presets were removed: init --maintainability writes explicit rule entries"
        )
    return message


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
