from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from smelt.engine.check import rule_meta
from smelt.engine.schema import config_json_schema
from smelt.rules.registry import build_rule_set, catalog_metadata

if TYPE_CHECKING:
    from smelt.rules.base import RuleContract

# Maintainer scripts outside the analyzed package use this generator entry point.

SCHEMA_FILE = "smelt.schema.json"
DOCS_DIR = "docs/rules"


def write_generated_files(
    root: Path,
) -> list[
    str
]:  # smelt: ignore[SMT429] -- Public maintainer entry point called by excluded scripts/generate.py.
    """Write the schema and the rule pages, and report what changed."""
    changed = []
    generated = _generated_files()
    for name, content in generated.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8", newline="\n")
            changed.append(name)
    for path in (root / "smelt/skills/smelt/references").rglob("*"):
        name = path.relative_to(root).as_posix()
        if path.is_file() and name not in generated:
            path.unlink()
            changed.append(name)
    return changed


def _generated_files() -> dict[str, str]:
    """Every generated file, keyed by its path relative to the repository root."""
    rules = build_rule_set().rules
    files = {SCHEMA_FILE: json.dumps(config_json_schema(), indent=2) + "\n"}
    files["smelt/rules/catalog.json"] = json.dumps(catalog_metadata(), indent=2) + "\n"
    files[f"{DOCS_DIR}/README.md"] = _rule_index(rules)
    for rule in rules:
        files[f"{DOCS_DIR}/{rule.code}.md"] = _rule_page(rule)
    # Ship the same manual offline; never maintain a second rule/CLI reference.
    root = Path(__file__).resolve().parent.parent
    manual = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((root / "docs").rglob("*"))
        if path.suffix in {".md", ".json"} and not path.is_relative_to(root / DOCS_DIR)
    }
    for name in ("README.md", "CONTRIBUTING.md", "CHANGELOG.md"):
        manual[name] = (root / name).read_text(encoding="utf-8")
    manual.update(files)
    files.update(
        {
            f"smelt/skills/smelt/references/{name}": content
            for name, content in manual.items()
            if name not in {"smelt/rules/catalog.json", SCHEMA_FILE}
        }
    )
    return files


def _rule_page(rule: RuleContract) -> str:
    """The reference page for one rule, the same content `smelt explain` prints."""
    doc = rule.explain()
    meta = rule_meta(rule)
    lines = [
        f"# {rule.code} {rule.name}",
        "",
        doc.summary,
        "",
        f"- **Category:** {meta.category.value}",
        f"- **Activation:** {meta.activation['mode']} — {meta.activation['description']}",
        f"- **Severity when active:** {meta.default_severity.value}",
        "",
        "## Why",
        "",
        doc.rationale,
        "",
        "## Bad",
        "",
        f"```{doc.example_language}",
        doc.bad,
        "```",
        "",
        "## Good",
        "",
        f"```{doc.example_language}",
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


def _rule_index(rules: list[RuleContract]) -> str:
    lines = [
        "# Rules",
        "",
        "| Code | Name | Category | Activation | Severity |",
        "| --- | --- | --- | --- | --- |",
    ]
    for rule in rules:
        meta = rule_meta(rule)
        lines.append(
            f"| [{rule.code}]({rule.code}.md) | {rule.name} | "
            f"{meta.category.value} | {meta.activation['mode']} | {meta.default_severity.value} |"
        )
    lines += ["", "`smelt rules` prints the same table in the terminal."]
    return "\n".join(lines) + "\n"
