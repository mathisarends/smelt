from __future__ import annotations

import textwrap
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from smelt.diagnostics.render.evidence import decision, subject_name
from smelt.diagnostics.render.text import fixable_summary, render_text

if TYPE_CHECKING:
    from smelt.diagnostics.report import LineReader, Report
    from smelt.diagnostics.violation import Violation

_WIDTH = 88
_SEPARATOR = " · "
_UNGROUPED = "Other"


@dataclass(frozen=True, slots=True)
class RuleBrief:
    """What a reader needs to judge the findings of one rule, from its documentation."""

    code: str
    name: str
    # Effective severity and whether the project enables the rule.
    status: str
    instruction: str
    smell: str = ""
    no_smell: str = ""
    # Replaces the advice of the individual findings, which then stay unlisted.
    repair: str = ""
    group_by: tuple[str, ...] = ()
    # Names a finding with ``expected`` keys in braces, such as "{class}.{method}".
    subject: str = ""
    # The rules entry to add once the findings are resolved; empty when enabled.
    enable: str = ""
    # The policy to declare before the rule can be enabled.
    requires: str = ""


def render_rule_view(report: Report, brief: RuleBrief, read_line: LineReader) -> str:
    """The report of a check that selects one rule: its brief, then its findings.

    Findings are listed by the decision they share and by file, and repair
    advice that several of them repeat is printed once.
    """
    findings = [v for v in report.violations if v.code == brief.code]
    if brief.repair:
        # The rule states the repair once; the per-finding wording adds names only.
        findings = [replace(v, hint=None) for v in findings]
    common = _common_hint(findings)
    lines = [
        *_brief_lines(brief, brief.repair or common),
        "",
        _counts(report, findings, brief),
        *_listing(findings, brief, common),
        "",
        *_closing(report, brief, resolved=not findings),
    ]
    others = [v for v in report.violations if v.code != brief.code]
    if others or report.configuration_diagnostics:
        rest = render_text(
            replace(report, violations=others), read_line, show_hints=True
        )
        lines.extend(["", "Other findings in this run:", rest.rstrip("\n")])
    return "\n".join(lines) + "\n"


def _brief_lines(brief: RuleBrief, fix: str) -> list[str]:
    lines = [
        f"{brief.code} {brief.name}{_SEPARATOR}{brief.status}",
        textwrap.fill(brief.instruction, width=_WIDTH, break_long_words=False),
    ]
    labelled = (("Smell", brief.smell), ("No smell", brief.no_smell), ("Fix", fix))
    lines.extend(_labelled(label, text) for label, text in labelled if text)
    return lines


def _counts(report: Report, findings: list[Violation], brief: RuleBrief) -> str:
    parts = [_plural(len(findings), "finding")]
    if findings:
        parts.append(_plural(len({v.path for v in findings}), "file"))
    groups = {decision(v, brief.group_by) for v in findings}
    if len(groups) > 1:
        parts.append(f"{len(groups)} groups")
    parts.extend(fixable_summary(findings))
    if report.in_debt:
        parts.append(f"{report.in_debt} in debt (--no-debt lists them)")
    if report.suppressed:
        parts.append(f"{report.suppressed} suppressed")
    return _SEPARATOR.join(parts)


def _listing(findings: list[Violation], brief: RuleBrief, common: str) -> list[str]:
    groups: dict[str, list[Violation]] = {}
    for violation in findings:
        groups.setdefault(decision(violation, brief.group_by), []).append(violation)
    if len(groups) <= 1:
        return _spaced(_file_blocks(findings, brief.subject, shown=common))
    lines: list[str] = []
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    for label, members in ordered:
        lines.extend(["", f"{label or _UNGROUPED} ({len(members)})"])
        shared = _common_hint(members) if len(members) > 1 else ""
        if shared and shared != common:
            lines.append(_labelled("Fix", shared))
        blocks = _file_blocks(
            members, brief.subject, shown=shared or common, indent="  "
        )
        for block in blocks:
            lines.extend(block)
    return lines


def _closing(report: Report, brief: RuleBrief, *, resolved: bool) -> list[str]:
    lines: list[str] = []
    if report.scope:
        lines.append(f"Scope: {', '.join(report.scope)}")
    if report.comparison:
        lines.append("Scope: findings new since the compared commit.")
    if resolved:
        state = f"No {brief.code} findings."
        enable = f" Enable it: {brief.enable}" if brief.enable else ""
    else:
        state = "Done when this command reports no findings."
        enable = f" Then enable it: {brief.enable}" if brief.enable else ""
    lines.append(state + enable)
    if brief.requires:
        lines.append(f"Requires: {brief.requires}")
    lines.append(
        f"Details: smelt explain {brief.code}{_SEPARATOR}evidence per finding: --verbose"
    )
    return lines


def _spaced(blocks: list[list[str]]) -> list[str]:
    """Blocks under one another; a blank line sets off those longer than one line."""
    lines: list[str] = []
    crowded = True
    for block in blocks:
        if crowded or len(block) > 1:
            lines.append("")
        lines.extend(block)
        crowded = len(block) > 1
    return lines


def _file_blocks(
    findings: list[Violation], subject: str, *, shown: str, indent: str = ""
) -> list[list[str]]:
    """The findings of each file; a file with one finding takes one line."""
    by_path: dict[str, list[Violation]] = {}
    for violation in findings:
        by_path.setdefault(violation.path or "<project>", []).append(violation)
    blocks: list[list[str]] = []
    for path in sorted(by_path):
        items = _item_lines(by_path[path], subject, shown=shown, indent=indent)
        if len(by_path[path]) == 1:
            blocks.append([f"{indent}{path}  {items[0].strip()}"])
        else:
            blocks.append([f"{indent}{path}", *items])
    return blocks


def _item_lines(
    findings: list[Violation], subject: str, *, shown: str, indent: str
) -> list[str]:
    """Findings named by their subject share lines; the others take one each."""
    prefix = f"{indent}  "
    lines: list[str] = []
    short: list[str] = []
    for violation in sorted(findings, key=lambda v: v.sort_key()):
        name = subject_name(violation, subject)
        hint = _squash(violation.hint or "")
        # A finding without a column describes its file, not line 1.
        located = violation.line and (violation.column or violation.line > 1)
        place = str(violation.line) if located else ""
        if name and hint in ("", shown):
            short.append(f"{place} {name}".strip())
            continue
        lines.extend(_packed(short, prefix))
        short = []
        detail = name or _squash(violation.message)
        repair = "" if hint in ("", shown) else f" -> {hint}"
        lines.append(
            f"{prefix}{place}  {detail}{repair}"
            if place
            else f"{prefix}{detail}{repair}"
        )
    lines.extend(_packed(short, prefix))
    return lines


def _packed(items: list[str], prefix: str) -> list[str]:
    """``items`` joined into lines no wider than the report, never split inside one."""
    lines: list[str] = []
    current = ""
    for item in items:
        candidate = f"{current}{_SEPARATOR}{item}" if current else f"{prefix}{item}"
        if current and len(candidate) > _WIDTH:
            lines.append(current)
            candidate = f"{prefix}{item}"
        current = candidate
    if current:
        lines.append(current)
    return lines


def _common_hint(findings: list[Violation]) -> str:
    """The repair advice every finding repeats verbatim, or nothing."""
    hints = {_squash(violation.hint or "") for violation in findings}
    return hints.pop() if len(hints) == 1 else ""


def _labelled(label: str, text: str) -> str:
    return textwrap.fill(
        text,
        width=_WIDTH,
        initial_indent=f"  {label + ':':<10}",
        subsequent_indent=" " * 12,
        break_on_hyphens=False,
        break_long_words=False,
    )


def _squash(text: str) -> str:
    return " ".join(text.split())


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"
