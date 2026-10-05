from __future__ import annotations

import json
from typing import TYPE_CHECKING

import yaml

from smelt.cli.support import EXIT_OK, CliError, Console, load_project_config
from smelt.config import config_json_schema
from smelt.config.errors import did_you_mean
from smelt.engine.check import rule_meta
from smelt.rules.registry import build_rule_set

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from smelt.rules.base import RuleSet


def explain(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    rules = build_rule_set()
    rule = rules.lookup(args.rule)
    if rule is None:
        msg = f'unknown rule "{args.rule}"{_suggestion(args.rule, rules)}'
        raise CliError(f"{msg}; `smelt rules` lists them all")
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
    lines.extend(["", f"Docs: {meta.docs_url}"])
    console.print("\n".join(lines))
    return EXIT_OK


def _suggestion(raw: str, rules: RuleSet) -> str:
    """A close rule name, or a code that differs in one character only."""
    wanted = raw.strip().upper()
    if not wanted[:1].isalpha() or not wanted[-1:].isdigit():
        return did_you_mean(raw.strip(), [r.name for r in rules.rules])
    close = [
        r.code
        for r in rules.rules
        if len(r.code) == len(wanted)
        and sum(a != b for a, b in zip(r.code, wanted, strict=True)) == 1
    ]
    return f' (did you mean "{close[0]}"?)' if len(close) == 1 else ""


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" if line else "" for line in text.splitlines())


def rules(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    rule_set = build_rule_set()
    metas = [rule_meta(rule) for rule in rule_set.rules]
    if args.format == "json":
        console.print(json.dumps([m.to_json() for m in metas], indent=2))
        return EXIT_OK
    for meta in metas:
        severity = meta.default_severity.value if meta.enabled_by_default else "off"
        console.print(
            f"{meta.code}  {meta.name:<28} {meta.category.value:<13} {severity}"
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
