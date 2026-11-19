from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from smelt.analysis.context import AnalysisContext
from smelt.cli.support import EXIT_OK, CliError, Console, load_project_config
from smelt.config import ConfigError
from smelt.config.errors import did_you_mean
from smelt.diagnostics.violation import Category
from smelt.engine.check import rule_meta
from smelt.engine.rule_settings import RuleSettings
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
    if args.format == "json":
        data = {
            **meta.to_json(),
            "rationale": doc.rationale,
            "bad": doc.bad,
            "good": doc.good,
            "fix": doc.fix,
            "config": list(doc.config),
            "example_language": doc.example_language,
        }
        console.print(json.dumps(data, indent=2))
        return EXIT_OK
    lines = [
        f"{rule.code} {rule.name}  [{rule.category.value}, activation: {meta.activation['mode']}, severity: {meta.default_severity.value}]",
        meta.activation["description"],
        "",
        doc.summary,
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
    ]
    if doc.config:
        lines.extend(["", "Config:", *(f"  {key}" for key in doc.config)])
    lines.extend(["", f"Docs: {meta.docs_url}"])
    console.print("\n".join(lines))
    return EXIT_OK


def rules(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    rule_set = build_rule_set()
    codes = (
        RuleSettings(
            rule_set, load_project_config(args.config, cwd)
        ).unconfigured_codes()
        if args.unconfigured
        else {rule.code for rule in rule_set.rules}
    )
    metas = [rule_meta(rule) for rule in rule_set.rules if rule.code in codes]
    if args.format == "json":
        console.print(json.dumps([m.to_json() for m in metas], indent=2))
        return EXIT_OK
    width = max((len(meta.name) for meta in metas), default=0)
    console.print(f"  {'code':<6}  {'rule':<{width}} {'enabled':<8} severity")
    for family in Category:
        members = [meta for meta in metas if meta.category is family]
        if not members:
            continue
        console.print(f"{family.value}:")
        for meta in members:
            console.print(
                f"  {meta.code}  {meta.name:<{width}} {meta.activation['mode']:<8} {meta.default_severity.value}"
            )
    modes = {meta.activation["mode"] for meta in metas}
    legend = [text for mode, text in _ACTIVATION_LEGEND if mode in modes]
    if legend:
        console.print("\n" + "\n".join(legend))
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


_ACTIVATION_LEGEND = (
    ("default", "default: runs without configuration"),
    (
        "policy",
        "policy: runs once its smelt.yml option is set; `smelt explain` names it",
    ),
    ("explicit", "explicit: runs only when listed under rules: in smelt.yml"),
)


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
