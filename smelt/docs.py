from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from smelt.diagnostics.violation import DOCS_BASE_URL
from smelt.engine.check import rule_meta
from smelt.engine.schema import config_json_schema
from smelt.rules.groups import RULE_GROUPS
from smelt.rules.registry import build_rule_set, catalog_metadata

if TYPE_CHECKING:
    from smelt.rules.base import RuleContract

# Maintainer scripts outside the analyzed package use this generator entry point.

SCHEMA_FILE = "smelt.schema.json"
DOCS_DIR = "docs/rules"


def check_generated_files(
    root: Path,
) -> list[
    str
]:  # smelt: ignore[SMT429] -- Public maintainer entry point called by excluded scripts/generate.py.
    """Report missing, changed and unexpected generated files without writing."""
    generated = _generated_files()
    drift = [
        name
        for name, content in generated.items()
        if not (root / name).is_file()
        or (root / name).read_text(encoding="utf-8") != content
    ]
    return sorted([*drift, *_unexpected_generated_files(root, generated)])


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
    for name in _unexpected_generated_files(root, generated):
        (root / name).unlink()
        changed.append(name)
    for path in sorted((root / _SKILL_REFERENCES).rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return changed


_SKILL_REFERENCES = "smelt/skills/smelt/references"
_SKILL_GUIDES = (
    "README.md",
    "CHANGELOG.md",
    "docs/adoption.md",
    "docs/architecture.md",
    "docs/cli.md",
    "docs/configuration.md",
    "docs/integrations.md",
    "docs/rule-selection.md",
    "docs/structure.md",
    "docs/testing.md",
    "docs/upgrading.md",
)


def _unexpected_generated_files(root: Path, generated: dict[str, str]) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for directory in (DOCS_DIR, _SKILL_REFERENCES)
        for path in (root / directory).rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() not in generated
    )


def _generated_files() -> dict[str, str]:
    """Every generated file, keyed by its path relative to the repository root."""
    rules = build_rule_set().rules
    files = {SCHEMA_FILE: json.dumps(config_json_schema(), indent=2) + "\n"}
    files["smelt/rules/catalog.json"] = json.dumps(catalog_metadata(), indent=2) + "\n"
    files[f"{DOCS_DIR}/README.md"] = _rule_index(rules)
    for rule in rules:
        files[f"{DOCS_DIR}/{rule.code}.md"] = _rule_page(rule)
    # Bundle consumer guides explicitly, never research data or proposals.
    root = Path(__file__).resolve().parent.parent
    manual = {name: (root / name).read_text(encoding="utf-8") for name in _SKILL_GUIDES}
    manual.update(
        {name: content for name, content in files.items() if name.startswith(DOCS_DIR)}
    )
    files.update(
        {
            f"{_SKILL_REFERENCES}/{name}": _skill_links(name, content, set(manual))
            for name, content in manual.items()
        }
    )
    return files


def _skill_links(name: str, content: str, bundled: set[str]) -> str:
    """Keep local guide links; point omitted maintainer material to the repository."""

    def target(match: re.Match[str]) -> str:
        original = match.group()
        parsed = urlsplit(original)
        if parsed.scheme or parsed.netloc or not parsed.path:
            return original
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(name), parsed.path)
        )
        if resolved in bundled:
            return original
        repository = DOCS_BASE_URL.removesuffix("/docs/rules")
        return f"{repository}/{resolved}{original[len(parsed.path) :]}"

    return re.sub(r"(?<=\]\()[^()\s]+(?=\))", target, content)


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
    if meta.groups:
        lines += [
            "",
            "## Rule groups",
            "",
            *(f"- `{name}` — `smelt explain {name}`" for name in meta.groups),
        ]
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
    lines += [
        "",
        "## Rule groups",
        "",
        "Enable a coherent policy with one `rules` entry. Individual settings override the group within the same map; closest-package settings still win.",
        "",
    ]
    for group in RULE_GROUPS.values():
        lines += [
            f"- `{group.name}`: {group.summary} Includes {', '.join(group.codes)}. Use `smelt explain {group.name}` for the scope and limits."
        ]
    lines += ["", "`smelt rules` prints the same catalog in the terminal."]
    return "\n".join(lines) + "\n"
