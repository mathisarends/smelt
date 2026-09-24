from __future__ import annotations

import json
from typing import TYPE_CHECKING

from smelt.config import config_json_schema
from smelt.engine.check import rule_meta
from smelt.rules.registry import build_rule_set

if TYPE_CHECKING:
    from pathlib import Path

    from smelt.rules.base import Rule

SCHEMA_FILE = "smelt.schema.json"
DOCS_DIR = "docs/rules"


def rule_page(rule: Rule) -> str:
    """The reference page for one rule, the same content `smelt explain` prints."""
    doc = rule.explain()
    meta = rule_meta(rule)
    default = meta.default_severity.value
    if not meta.enabled_by_default:
        default = f"off by default ({default} when enabled)"
    lines = [
        f"# {rule.code} {rule.name}",
        "",
        doc.summary,
        "",
        f"- **Category:** {meta.category.value}",
        f"- **Default severity:** {default}",
        "",
        "## Why",
        "",
        doc.rationale,
        "",
        "## Bad",
        "",
        "```python",
        doc.bad,
        "```",
        "",
        "## Good",
        "",
        "```python",
        doc.good,
        "```",
        "",
        "## How to fix",
        "",
        doc.fix,
    ]
    if doc.config:
        lines += ["", "## Config", "", *(f"- `{key}`" for key in doc.config)]
    lines += ["", f"Explain it in the terminal with `smelt explain {rule.code}`."]
    return "\n".join(lines) + "\n"


def rule_index(rules: list[Rule]) -> str:
    lines = [
        "# Rules",
        "",
        "| Code | Name | Category | Default |",
        "| --- | --- | --- | --- |",
    ]
    for rule in rules:
        meta = rule_meta(rule)
        default = meta.default_severity.value if meta.enabled_by_default else "off"
        lines.append(
            f"| [{rule.code}]({rule.code}.md) | {rule.name} | "
            f"{meta.category.value} | {default} |"
        )
    lines += ["", "`smelt rules` prints the same table in the terminal."]
    return "\n".join(lines) + "\n"


def generated_files() -> dict[str, str]:
    """Every generated file, keyed by its path relative to the repository root."""
    rules = build_rule_set(load_entry_points=False).rules
    files = {SCHEMA_FILE: json.dumps(config_json_schema(), indent=2) + "\n"}
    files[f"{DOCS_DIR}/README.md"] = rule_index(rules)
    for rule in rules:
        files[f"{DOCS_DIR}/{rule.code}.md"] = rule_page(rule)
    return files


def write_generated_files(  # smelt: ignore[SMT408] -- called by scripts/generate.py
    root: Path,
) -> list[str]:
    """Write the schema and the rule pages, and report what changed."""
    changed = []
    for name, content in generated_files().items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8", newline="\n")
            changed.append(name)
    return changed
