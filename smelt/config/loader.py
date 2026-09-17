from __future__ import annotations

import re
import types
import typing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ValidationError

from smelt.config.errors import ConfigError, ConfigIssue, did_you_mean, format_loc
from smelt.config.models import SmeltConfig
from smelt.config.validation import validate_semantics

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

CONFIG_FILENAME = "smelt.yaml"
CONFIG_FILENAMES = (CONFIG_FILENAME, "smelt.yml")


class _StrictBoolLoader(yaml.SafeLoader):
    """YAML 1.2 booleans: only ``true``/``false``, so ``off`` stays a string."""


_StrictBoolLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictBoolLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def load_yaml(text: str) -> object:
    return yaml.load(text, Loader=_StrictBoolLoader)  # noqa: S506


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    config: SmeltConfig
    path: Path
    warnings: tuple[ConfigIssue, ...] = ()

    @property
    def root(self) -> Path:
        """Project root: the directory containing the config file."""
        return self.path.parent


def discover_config(start: Path) -> Path | None:
    """Walk up from ``start`` looking for ``smelt.yaml`` or ``smelt.yml``."""
    current = start.resolve()
    for directory in (current, *current.parents):
        for filename in CONFIG_FILENAMES:
            candidate = directory / filename
            if candidate.is_file():
                return candidate
    return None


def load_config(path: Path) -> LoadedConfig:
    source = path.name
    try:
        raw = load_yaml(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError([ConfigIssue("", f"cannot read {path}: {exc}")]) from exc
    except yaml.YAMLError as exc:
        raise ConfigError([ConfigIssue("", f"invalid YAML: {exc}")], source) from exc
    config, warnings = parse_config(raw, source=source)
    return LoadedConfig(config=config, path=path.resolve(), warnings=warnings)


def parse_config(
    raw: object, *, source: str | None = None
) -> tuple[SmeltConfig, tuple[ConfigIssue, ...]]:
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
    errors, warnings = validate_semantics(config)
    if errors:
        raise ConfigError(errors, source)
    return config, tuple(warnings)


def _issues_from_validation(exc: ValidationError) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    for error in exc.errors():
        loc = tuple(error["loc"])
        message = str(error["msg"]).removeprefix("Value error, ")
        if error["type"] == "extra_forbidden":
            key = str(loc[-1])
            known = _known_fields(SmeltConfig, loc[:-1])
            message = f'unknown key "{key}"{did_you_mean(key, known)}'
        elif error["type"] == "missing":
            message = "required key is missing"
        elif error["type"] == "literal_error":
            message = f"{message}, got {error.get('input')!r}"
        issues.append(ConfigIssue(format_loc(loc), message))
    return issues


def _known_fields(model: type[BaseModel], loc: Sequence[str | int]) -> list[str]:
    current: Any = model
    for item in loc:
        current = _step(current, item)
        if current is None:
            return []
    if isinstance(current, type) and issubclass(current, BaseModel):
        return list(current.model_fields)
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
