from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from smelt.analysis.context import AnalysisContext
from smelt.cli.support import EXIT_OK, CliError, Console, load_project_config
from smelt.config import ConfigError, discover_config
from smelt.config.errors import did_you_mean
from smelt.diagnostics.violation import Category
from smelt.engine.check import rule_meta
from smelt.engine.rule_settings import (
    RuleSettings,
    RuleStatus,
    catalog_status,
    rule_usage,
)
from smelt.engine.schema import config_json_schema
from smelt.rules.registry import build_rule_set
from smelt.rules.renames import retired_hint

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from smelt.rules.base import RuleSet


def explain(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    rules = build_rule_set()
    if (family := rules.family_codes(args.rule)) is not None:
        members = [
            rule_meta(rule).to_json() for rule in rules.rules if rule.code in family
        ]
        name = args.rule.strip().lower().removesuffix("/")
        if args.format == "json":
            console.print(json.dumps({"family": name, "rules": members}, indent=2))
        else:
            console.print(
                f"{name} [rule family]\n\nExplore: smelt check --select {name}\n"
            )
            for member in members:
                console.print(
                    f"  {member['code']} {member['name']}  {member['summary']}"
                )
        return EXIT_OK
    rule = rules.lookup(args.rule)
    if rule is None:
        msg = f'unknown rule "{args.rule}"{_suggestion(args.rule, rules)}'
        raise CliError(
            f"{msg}; `smelt rules` lists them all", option="rule", value=args.rule
        )
    doc = rule.explain()
    meta = rule_meta(rule)
    usage = rule_usage(rule)
    settings = _project_settings(args, cwd, rules, required=False)
    status = settings.status(rule) if settings else None
    if args.format == "json":
        data = {
            **meta.to_json(),
            "enable": usage.enable,
            "options": list(usage.options),
            "requires_policy": usage.requires,
            "status": _status_json(status) if status else None,
            "rationale": doc.rationale,
            "bad": doc.bad,
            "good": doc.good,
            "fix": doc.fix,
            "canonical": doc.canonical,
            "config": list(doc.config),
            "example_language": doc.example_language,
        }
        console.print(json.dumps(data, indent=2))
        return EXIT_OK
    lines = [
        f"{rule.code} {rule.name}  [{rule.category.value}, {meta.default_severity.value}]",
        doc.summary,
        "",
        f"Enable:    {usage.enable}",
        f"Options:   {', '.join(usage.options) or 'none'}",
        f"Requires:  {usage.requires or 'nothing'}",
    ]
    if settings is not None and status is not None:
        lines.append(f"Status:    {_status_text(status)} in {settings.source}")
    lines.append(f"Guide:     {doc.canonical}")
    lines.extend(
        [
            "",
            "Why:",
            _indent(doc.rationale),
            "",
            "Bad:",
            _indent(doc.bad),
            "",
            "Good:",
            _indent(doc.good),
            "",
            "How to fix:",
            _indent(doc.fix),
            "",
            f"Docs: {meta.docs_url}",
        ]
    )
    console.print("\n".join(lines))
    return EXIT_OK


def rules(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    rule_set = build_rule_set()
    settings = _project_settings(args, cwd, rule_set, required=args.unconfigured)
    codes = (
        settings.unconfigured_codes()
        if settings is not None and args.unconfigured
        else {rule.code for rule in rule_set.rules}
    )
    selected = [rule for rule in rule_set.rules if rule.code in codes]
    statuses = {
        rule.code: settings.status(rule) if settings else catalog_status(rule)
        for rule in selected
    }
    metas = [rule_meta(rule) for rule in selected]
    if args.format == "json":
        console.print(
            json.dumps(
                [
                    {**meta.to_json(), **_status_json(statuses[meta.code])}
                    for meta in metas
                ],
                indent=2,
            )
        )
        return EXIT_OK
    width = max((len(meta.name) for meta in metas), default=0)
    console.print(f"  {'code':<6}  {'rule':<{width}} {'status':<12} severity")
    for family in Category:
        members = [meta for meta in metas if meta.category is family]
        if not members:
            continue
        console.print(f"{family.value}:")
        for meta in members:
            status = statuses[meta.code]
            severity = status.severity or meta.default_severity.value
            console.print(
                f"  {meta.code}  {meta.name:<{width}} {status.state:<12} {severity}"
            )
    states = {status.state for status in statuses.values()}
    scope = f"in {settings.source}" if settings else "without a smelt.yml"
    legend = [text for state, text in _STATUS_LEGEND if state in states]
    if legend:
        console.print(f"\nstatus {scope}:\n" + "\n".join(legend))
    return EXIT_OK


def config_show(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    loaded = load_project_config(args.config, cwd)
    try:
        settings = RuleSettings(build_rule_set(lazy=True), loaded)
        if loaded.config.members or args.sources:
            settings.validate_packages(
                AnalysisContext(loaded.root, loaded.config, store=loaded.source)
            )
    except ConfigError as exc:
        explained = loaded.explain_error(exc)
        if explained is exc:
            raise
        raise explained from exc
    config = loaded.config
    base = config.workspace_base
    data = (base or config).model_dump(
        mode="json",
        by_alias=True,
        exclude={"members"},
        exclude_none=base is not None,
        exclude_unset=base is not None,
    )
    if args.sources:
        data = {
            "config": data,
            "sources": [path.as_posix() for path in loaded.inputs],
            "origins": {
                key: source for key, source in loaded.origins.items() if "[" not in key
            },
            "rule_selectors": settings.selector_rules,
        }
        if config.project.workspace == "uv":
            data["workspace_members"] = {
                path: member.model_dump(mode="json", by_alias=True, exclude_unset=True)
                for path, member in config.members.items()
            }
        if loaded.warnings:
            data["warnings"] = [str(issue) for issue in loaded.warnings]
    if args.format == "json":
        console.print(json.dumps(data, indent=2))
    else:
        console.print(f"# resolved from {loaded.path.as_posix()}")
        console.print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), end="")
    return EXIT_OK


def config_schema(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    console.print(json.dumps(config_json_schema(), indent=2))
    return EXIT_OK


_STATUS_LEGEND = (
    ("on", "  on: runs project-wide at the shown severity"),
    (
        "scoped",
        "  scoped: runs only where a style override or workspace member enables it",
    ),
    ("off", "  off: not enabled; add it under rules: (catalog severity shown)"),
    (
        "needs policy",
        "  needs policy: declare its policy before enabling it; `smelt explain CODE` names it",
    ),
)


def _project_settings(
    args: argparse.Namespace, cwd: Path, rules: RuleSet, *, required: bool
) -> RuleSettings | None:
    """Rule settings of the project config, or None outside a configured project."""
    config = getattr(args, "config", None)
    if not required and config is None and discover_config(cwd) is None:
        return None
    try:
        return RuleSettings(rules, load_project_config(config, cwd))
    except (ConfigError, CliError):
        if required:
            raise
        return None


def _status_text(status: RuleStatus) -> str:
    return f"{status.state} ({status.severity})" if status.severity else status.state


def _status_json(status: RuleStatus) -> dict[str, str | None]:
    return {"status": status.state, "severity": status.severity}


def _suggestion(raw: str, rules: RuleSet) -> str:
    """A close rule name, or a code that differs in one character only."""
    codes = {rule.name: rule.code for rule in rules.rules}
    if retired := retired_hint(raw.strip(), codes):
        return retired
    wanted = raw.strip().upper()
    if not wanted[:1].isalpha() or not wanted[-1:].isdigit():
        return did_you_mean(raw.strip(), codes)
    close = [
        r.code
        for r in rules.rules
        if len(r.code) == len(wanted)
        and sum(a != b for a, b in zip(r.code, wanted, strict=True)) == 1
    ]
    return f' (did you mean "{close[0]}"?)' if len(close) == 1 else ""


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" if line else "" for line in text.splitlines())
