from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from smelt.cli.support import EXIT_OK, CliError, Console, load_project_config
from smelt.config import config_json_schema
from smelt.config.errors import did_you_mean
from smelt.engine.check import load_rules, rule_meta
from smelt.rules.registry import build_rule_set

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from smelt.rules.base import RuleSet


def _rule_set(args: argparse.Namespace, cwd: Path) -> RuleSet:
    try:
        loaded = load_project_config(args.config, cwd)
    except CliError:
        return build_rule_set()
    return load_rules(loaded)


def explain(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    rules = _rule_set(args, cwd)
    rule = rules.lookup(args.rule)
    if rule is None:
        candidates = [r.code for r in rules.rules] + [r.name for r in rules.rules]
        msg = f'unknown rule "{args.rule}"{did_you_mean(args.rule, candidates)}'
        raise CliError(msg)
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
        }
        console.print(json.dumps(data, indent=2))
        return EXIT_OK
    default = meta.default_severity.value
    if not meta.enabled_by_default:
        default = f"off (opt-in, {default} when enabled)"
    lines = [
        f"{rule.code} {rule.name}  [{rule.category.value}, default: {default}]",
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
    if rule.fixable:
        lines.extend(["", f"Fixable: smelt fix {rule.code}"])
    lines.extend(["", f"Docs: {meta.docs_url}"])
    console.print("\n".join(lines))
    return EXIT_OK


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" if line else "" for line in text.splitlines())


def rules(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    rule_set = _rule_set(args, cwd)
    metas = [rule_meta(rule) for rule in rule_set.rules]
    if args.format == "json":
        console.print(json.dumps([m.to_json() for m in metas], indent=2))
        return EXIT_OK
    for meta in metas:
        severity = meta.default_severity.value if meta.enabled_by_default else "off"
        fixable = "fixable" if meta.fixable else ""
        console.print(
            f"{meta.code}  {meta.name:<28} {meta.category.value:<13} {severity:<8} {fixable}".rstrip()
        )
    return EXIT_OK


def config_show(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    loaded = load_project_config(args.config, cwd)
    data = loaded.config.model_dump(mode="json")
    if args.format == "json":
        console.print(json.dumps(data, indent=2))
    else:
        console.print(f"# resolved from {loaded.path.as_posix()}")
        console.print(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), end="")
    return EXIT_OK


def config_schema(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    console.print(json.dumps(config_json_schema(), indent=2))
    return EXIT_OK
