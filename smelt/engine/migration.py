"""Round-trip a legacy policy into a version 2 file with the 0.14 keys and rules."""

from __future__ import annotations

import io
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError
from ruamel.yaml.tokens import CommentToken

from smelt.config.errors import ConfigError, ConfigIssue
from smelt.config.models import LIMIT_RULES, UNCOVERED_SOURCE, PackageBoundary
from smelt.rules.registry import build_rule_set
from smelt.rules.renames import replacement_selectors

if TYPE_CHECKING:
    from collections.abc import Sequence

    from smelt.rules.base import RuleSet

_LEVELS = ("off", "hint", "warning", "error")


@dataclass(frozen=True, slots=True)
class Migration:
    text: str
    notes: tuple[str, ...]


def migrate_policy(text: str) -> Migration:
    yaml, root = _load(text)
    before = deepcopy(root)
    notes: list[str] = []
    _structure(root)
    _coverage(root, notes)
    catalog = build_rule_set(lazy=True)
    _rules(root.get("rules"), catalog, notes)
    style = root.get("style")
    if isinstance(style, dict) and isinstance(
        overrides := style.get("overrides"), dict
    ):
        for policy in overrides.values():
            if isinstance(policy, dict):
                _rules(policy.get("rules"), catalog, notes)
    _limits(root, catalog, notes)
    if root.get("version") == 1:
        root["version"] = 2
        notes.append("Updated version 1 to version 2.")
    ignore = root.get("ignore")
    if isinstance(ignore, list):
        for index in reversed(range(len(ignore))):
            entry = ignore[index]
            if not isinstance(entry, dict) or not isinstance(
                old := entry.get("rule"), str
            ):
                continue
            replacements = replacement_selectors(old)
            if replacements:
                entry["rule"] = replacements[0]
                for new in reversed(replacements[1:]):
                    extra = deepcopy(entry)
                    extra["rule"] = new
                    ignore.insert(index + 1, extra)
    return _dump(yaml, root, before, text=text, notes=notes)


def enable_rules(
    text: str, entries: Sequence[tuple[tuple[str, ...], str, str]]
) -> Migration:
    """Add ``name: severity`` to each rules map, at its path, that lacks the rule.

    A declared policy no longer enables its rule, so a migration names every
    rule the policy used to enable.
    """
    yaml, root = _load(text)
    before = deepcopy(root)
    notes: list[str] = []
    for path, name, severity in entries:
        settings = root
        for key in path:
            settings = _mapping(settings, key)
        if name not in settings:
            _append(settings, name, severity)
            notes.append(
                f"Enabled {name} in {'.'.join(path)}; its policy enabled it implicitly before."
            )
    return _dump(yaml, root, before, text=text, notes=notes)


def _load(text: str) -> tuple[YAML, CommentedMap]:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 100
    yaml.indent(mapping=2, sequence=4, offset=2)
    try:
        root = yaml.load(text)
    except YAMLError as exc:
        raise ConfigError([ConfigIssue("", str(exc))]) from exc
    if not isinstance(root, CommentedMap):
        raise ConfigError([ConfigIssue("", "expected a YAML mapping")])
    return yaml, root


def _dump(
    yaml: YAML, root: CommentedMap, before: CommentedMap, *, text: str, notes: list[str]
) -> Migration:
    if root == before:
        return Migration(text, tuple(notes))
    output = io.StringIO()
    yaml.dump(root, output)
    return Migration(output.getvalue(), tuple(notes))


def _coverage(root: CommentedMap, notes: list[str]) -> None:
    project = root.get("project")
    if not isinstance(project, CommentedMap) or "coverage" not in project:
        return
    mode = project.pop("coverage")
    if not project:
        del root["project"]
    if mode != "strict":
        notes.append("Removed project.coverage; configured coverage is the default.")
        return
    rules = _mapping(root, "rules")
    if not any(key in rules for key in UNCOVERED_SOURCE):
        _append(rules, UNCOVERED_SOURCE[1], "error")
    notes.append(
        f"Replaced project.coverage: strict with rules.{UNCOVERED_SOURCE[1]}: error."
    )


class _LimitScope(NamedTuple):
    """Where limit options are read and the mapping whose rules receive them."""

    location: str
    options: Any
    owner: CommentedMap
    refines: bool


def _limits(root: CommentedMap, catalog: RuleSet, notes: list[str]) -> None:
    """Move each max_* option into the max of its rule entry in the same scope."""
    style = root.get("style")
    scopes = [
        _LimitScope("style", style, root, refines=False),
        _LimitScope("tests", root.get("tests"), root, refines=False),
    ]
    enabled = _enabled(root.get("rules"))
    if isinstance(style, CommentedMap) and isinstance(
        overrides := style.get("overrides"), CommentedMap
    ):
        scopes.extend(
            _LimitScope(f"style.overrides.{name}", policy, policy, refines=True)
            for name, policy in overrides.items()
            if isinstance(policy, CommentedMap)
        )
    for location, options, owner, refines in scopes:
        if not isinstance(options, CommentedMap):
            continue
        for option, (code, name) in LIMIT_RULES.items():
            if option not in options:
                continue
            value = options.pop(option)
            reason = None
            if isinstance(value, dict) and "value" in value:
                value, reason = value["value"], value.get("reason")
            rules = _mapping(owner, "rules")
            key = next((k for k in (name, code) if k in rules), None)
            if key is None:
                entry = CommentedMap()
                # An override refines a rule the root enables; otherwise the
                # option enabled the rule in its own scope.
                if not (refines and (name in enabled or code in enabled)):
                    rule = catalog.lookup(code)
                    entry["severity"] = (
                        rule.default_severity.value if rule else "warning"
                    )
                entry["max"] = value
                if reason is not None:
                    entry["reason"] = reason
                entry.fa.set_flow_style()
                _append(rules, name, entry)
                key = name
            elif isinstance(current := rules[key], dict):
                current["max"] = value
            else:
                entry = CommentedMap([("severity", current), ("max", value)])
                entry.fa.set_flow_style()
                rules[key] = entry
            prefix = "" if owner is root else f"{location}."
            notes.append(f"Moved {location}.{option} to {prefix}rules.{key}.max.")
        if not options and owner is root:
            del root[location]


def _enabled(settings: Any) -> set[str]:
    if not isinstance(settings, dict):
        return set()
    return {
        key
        for key, value in settings.items()
        if (value.get("severity") if isinstance(value, dict) else value)
        not in (None, "off")
    }


def _structure(root: CommentedMap) -> None:
    architecture = root.get("architecture")
    if isinstance(architecture, CommentedMap):
        _move(architecture, "imports", architecture, new="import_graph")
    tests = root.get("tests")
    if isinstance(tests, CommentedMap):
        _move(tests, "unmirrored", tests, new="mirror_exclude")
        _move(tests, "forbid_mocks_in", tests, new="forbidden_mock_paths")
    _move(root, "structure", root, new="style")
    style = root.get("style")
    if not isinstance(style, CommentedMap):
        return
    _move(style, "module_import_exempt", style, new="module_import_exemptions")
    if "canonical_exports" in style:
        _move(
            style,
            "canonical_exports",
            _mapping(root, "architecture"),
            new="canonical_exports",
        )
    if "contracts" not in style:
        return
    _move(style, "contracts", style, new="overrides")
    overrides = style.get("overrides")
    if not isinstance(overrides, CommentedMap):
        return
    _split_contracts(overrides, root)
    if not overrides:
        del style["overrides"]


def _split_contracts(overrides: CommentedMap, root: CommentedMap) -> None:
    for name, policy in list(overrides.items()):
        if not isinstance(policy, CommentedMap):
            continue
        _move(policy, "module_import_exempt", policy, new="module_import_exemptions")
        _move(policy, "bundle_imports_at", policy, new="min_imports_to_bundle")
        _move(policy, "export_at", policy, new="min_importers_to_export")
        boundary = [
            key
            for key in policy
            if key != "match" and key in PackageBoundary.model_fields
        ]
        if not boundary:
            continue
        packages = _mapping(_mapping(root, "architecture"), "packages")
        if len(boundary) + int("match" in policy) == len(policy):
            _move(overrides, name, packages, new=name)
            continue
        if name in packages:
            raise ConfigError(
                [
                    ConfigIssue(
                        name,
                        "package policy already exists; reconcile the contracts manually",
                    )
                ]
            )
        package = _mapping(packages, name)
        if "match" in policy:
            package["match"] = policy["match"]
        for key in boundary:
            _move(policy, key, package, new=key)


def _rules(settings: Any, catalog: RuleSet, notes: list[str]) -> None:
    if not isinstance(settings, CommentedMap):
        return
    for old in list(settings):
        if not isinstance(old, str) or not (replacements := replacement_selectors(old)):
            continue
        value = settings[old]
        for key in replacements:
            rule = catalog.lookup(key)
            if rule is None:
                raise ConfigError(
                    [
                        ConfigIssue(
                            old,
                            f"unknown replacement {key}; review this selector manually",
                        )
                    ]
                )
            # Name and code are alternative spellings of the same setting.
            new = next((k for k in (rule.name, rule.code) if k in settings), rule.name)
            if new in settings:
                winner = max(
                    (settings[new], value),
                    key=lambda v: _severity(v, rule.default_severity.value),
                )
                settings[new] = winner
                # ruamel attaches inline and following comment lines to the value.
                if (
                    key == replacements[0]
                    and (comments := settings.ca.items.get(old))
                    and (token := comments[2])
                ):
                    lines = [
                        line.strip().removeprefix("#").lstrip()
                        for line in token.value.splitlines()
                    ]
                    settings.yaml_set_comment_before_after_key(
                        new, before="\n".join(lines), indent=settings.lc.col
                    )
                notes.append(f"Merged {old} into {new}, retaining severity {winner}.")
            elif key == replacements[0]:
                _move(settings, old, settings, new=new)
            else:
                settings[new] = deepcopy(value)
        settings.pop(old, None)


def _severity(setting: Any, default: str) -> int:
    value = setting.get("severity") if isinstance(setting, dict) else setting
    if value == "on":
        value = default
    if value not in _LEVELS:
        raise ConfigError(
            [ConfigIssue("rules", f"cannot merge unknown severity {value!r}")]
        )
    return _LEVELS.index(value)


def _append(mapping: CommentedMap, key: str, value: object) -> None:
    """Append a key, keeping the blank lines and comments that close the mapping after it."""
    last = next(reversed(mapping), None) if mapping else None
    comments = mapping.ca.items.get(last) if last is not None else None
    token = comments[2] if comments and len(comments) > 2 else None  # noqa: PLR2004 - ruamel keeps the value comment third
    mapping[key] = value
    if token is None:
        return
    # An inline comment stays with its entry; the lines after it close the mapping.
    inline, newline, closing = token.value.partition("\n")
    if not inline.strip():
        mapping.ca.items[key] = mapping.ca.items.pop(last)
        return
    if not closing:
        return
    token.value = inline + newline
    mapping.ca.items[key] = [
        None,
        None,
        CommentToken(newline + closing, token.start_mark),
        None,
    ]


def _mapping(parent: CommentedMap, name: str) -> CommentedMap:
    value = parent.setdefault(name, CommentedMap())
    if not isinstance(value, CommentedMap):
        raise ConfigError(
            [ConfigIssue(name, "expected a mapping; migrate this section manually")]
        )
    return value


def _move(source: CommentedMap, old: str, target: CommentedMap, *, new: str) -> None:
    if old not in source:
        return
    if new in target:
        raise ConfigError(
            [
                ConfigIssue(
                    old,
                    f"destination {new} already exists; reconcile the entries manually",
                )
            ]
        )
    index = list(source).index(old) if source is target else len(target)
    comment = source.ca.items.pop(old, None)
    target.insert(index, new, source.pop(old))
    if comment is not None:
        target.ca.items[new] = comment
