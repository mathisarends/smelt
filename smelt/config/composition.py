"""Explicit local policy composition, independent of configuration defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from smelt.config.errors import ConfigError, ConfigIssue, format_loc
from smelt.config.models import PackagePolicy
from smelt.config.workspace import member_raw_issues

if TYPE_CHECKING:
    from collections.abc import Callable

    from smelt.config.source import SourceStore

_MAX_DEPTH = 32


@dataclass(slots=True)
class ConfigComposition:
    raw: dict[str, Any] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)
    origins: dict[str, str] = field(default_factory=dict)
    rule_layers: list[dict[str, Any]] = field(default_factory=list)

    @property
    def composite(self) -> bool:
        """Whether values can come from more than one file."""
        return len(self.files) > 1

    def source_for(self, location: str) -> str | None:
        candidates = [
            key
            for key in self.origins
            if location == key or location.startswith((key + ".", key + "["))
        ]
        return self.origins[max(candidates, key=len)] if candidates else None


def compose_config(
    path: Path, store: SourceStore, parse_yaml: Callable[[str], object]
) -> ConfigComposition:
    """Read one parent per file; merge raw values before applying any defaults."""
    composed = ConfigComposition()
    _visit(store.resolve(path), store, parse_yaml, parents=(), composed=composed)
    return composed


def add_member(
    composed: ConfigComposition,
    member: str,
    path: Path,
    *,
    store: SourceStore,
    parse_yaml: Callable[[str], object],
) -> None:
    """Place a member's own config under ``members.<path>``; it inherits the root."""
    source = path.as_posix()
    try:
        raw = parse_yaml(store.read_text(path))
    except (OSError, UnicodeError) as exc:
        raise ConfigError(
            [ConfigIssue("", f"cannot read {path}: {exc}")], source
        ) from exc
    except yaml.YAMLError as exc:
        raise ConfigError([ConfigIssue("", f"invalid YAML: {exc}")], source) from exc
    raw = {} if raw is None else raw
    issues = member_raw_issues(raw)
    if issues:
        raise ConfigError(issues, source)
    _check_tree(raw, (), frozenset(), source=source)
    _merge(
        composed.raw.setdefault("members", {}),
        {member: raw},
        ("members",),
        source=source,
        origins=composed.origins,
    )
    composed.files.append(path)


def _visit(
    path: Path,
    store: SourceStore,
    parse_yaml: Callable[[str], object],
    *,
    parents: tuple[Path, ...],
    composed: ConfigComposition,
) -> None:
    source = path.as_posix()
    if path in parents:
        chain = " -> ".join(p.as_posix() for p in (*parents, path))
        raise ConfigError(
            [ConfigIssue("extends", f"configuration inheritance cycle: {chain}")],
            parents[-1].as_posix(),
        )
    if len(parents) >= _MAX_DEPTH:
        raise ConfigError(
            [
                ConfigIssue(
                    "extends", f"configuration inheritance exceeds {_MAX_DEPTH} files"
                )
            ],
            parents[-1].as_posix(),
        )
    try:
        raw = parse_yaml(store.read_text(path))
    except (OSError, UnicodeError) as exc:
        referring = parents[-1].as_posix() if parents else source
        raise ConfigError(
            [ConfigIssue("extends" if parents else "", f"cannot read {path}: {exc}")],
            referring,
        ) from exc
    except yaml.YAMLError as exc:
        raise ConfigError([ConfigIssue("", f"invalid YAML: {exc}")], source) from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError([ConfigIssue("", "the top level must be a mapping")], source)
    _check_tree(raw, (), frozenset(), source=source)
    if "extends" in raw:
        parent = raw["extends"]
        if isinstance(parent, str) and parent.startswith("smelt:"):
            raise ConfigError(
                [
                    ConfigIssue(
                        "extends",
                        "built-in presets were removed; list rules individually (smelt init --maintainability writes a starting list) or extend a local base file",
                    )
                ],
                source,
            )
        if (
            not isinstance(parent, str)
            or not parent.strip()
            or Path(parent).anchor
            or "://" in parent
        ):
            raise ConfigError(
                [
                    ConfigIssue(
                        "extends",
                        "must be one nonempty local relative file path",
                    )
                ],
                source,
            )
        _visit(
            store.resolve(path.parent / parent),
            store,
            parse_yaml,
            parents=(*parents, path),
            composed=composed,
        )
    _merge(
        composed.raw,
        {k: v for k, v in raw.items() if k != "extends"},
        (),
        source=source,
        origins=composed.origins,
    )
    _collect_rules(raw, composed)
    composed.files.append(path)


def _collect_rules(raw: dict[str, Any], composed: ConfigComposition) -> None:
    """Keep each file's rules map so a later file's selection can replace it."""
    rules = raw.get("rules")
    if isinstance(rules, dict) and rules:
        composed.rule_layers.append(dict(rules))


def _check_tree(
    value: Any, location: tuple[str | int, ...], parents: frozenset[int], *, source: str
) -> None:
    if not isinstance(value, (dict, list)):
        return
    if id(value) in parents:
        raise ConfigError(
            [
                ConfigIssue(
                    format_loc(location), "recursive YAML values are not supported"
                )
            ],
            source,
        )
    ancestors = parents | {id(value)}
    children = value.items() if isinstance(value, dict) else enumerate(value)
    for key, child in children:
        _check_tree(child, (*location, key), ancestors, source=source)


def _merge(
    target: dict[str, Any],
    incoming: dict[str, Any],
    prefix: tuple[str | int, ...],
    *,
    source: str,
    origins: dict[str, str],
) -> None:
    for key, value in incoming.items():
        location = (*prefix, key)
        shown = format_loc(location)
        previous = target.get(key)
        if (
            isinstance(value, dict)
            and isinstance(previous, dict)
            and not _replace_style_value(location, value, previous)
        ):
            _merge(previous, value, location, source=source, origins=origins)
        else:
            for old in list(origins):
                if old == shown or old.startswith((shown + ".", shown + "[")):
                    del origins[old]
            if isinstance(value, dict):
                target[key] = {}
                _merge(target[key], value, location, source=source, origins=origins)
            else:
                target[key] = value
            origins[shown] = source
        if isinstance(value, list):
            _list_origins(value, location, source, origins=origins)


def _replace_style_value(
    location: tuple[str | int, ...], value: dict[Any, Any], previous: dict[Any, Any]
) -> bool:
    """A reason belongs to the whole style option written by one file."""
    match location:
        case ("style", name) | ("style", "overrides", _, name) if (
            name in PackagePolicy.model_fields
        ):
            return bool({"value", "reason"} & (previous.keys() | value.keys()))
        case _:
            return False


def _list_origins(
    values: list[Any],
    prefix: tuple[str | int, ...],
    source: str,
    *,
    origins: dict[str, str],
) -> None:
    for index, value in enumerate(values):
        location = (*prefix, index)
        origins[format_loc(location)] = source
        if isinstance(value, dict):
            _merge({}, value, location, source=source, origins=origins)
        elif isinstance(value, list):
            _list_origins(value, location, source, origins=origins)
