from __future__ import annotations

import itertools
import posixpath
import textwrap
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from smelt.diagnostics.groups import group_findings
from smelt.diagnostics.violation import Severity, Violation

if TYPE_CHECKING:
    from smelt.diagnostics.report import LineReader, Report

_WIDTH = 88
_TOP_EDGES = 5
# Keys of "expected" that only machine readers need; the message says the same.
_MACHINE_ONLY = frozenset({"cycle", "subject", "evidence"})
_COLORS = {
    Severity.ERROR: "\x1b[31m",
    Severity.WARNING: "\x1b[33m",
    Severity.HINT: "\x1b[36m",
}
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_YELLOW = "\x1b[33m"
_RESET = "\x1b[0m"
# Longer listings end with a per-rule breakdown so the overview survives scrolling.
_STATISTICS_THRESHOLD = 10


class _Style:
    def __init__(self, color: bool) -> None:
        self.color = color

    def __call__(self, text: str, *codes: str) -> str:
        if not self.color or not codes:
            return text
        return "".join(codes) + text + _RESET


def render_text(
    report: Report,
    read_line: LineReader,
    *,
    color: bool = False,
    show_hints: bool = False,
    statistics: bool = False,
) -> str:
    style = _Style(color)
    shown = [
        violation
        for violation in report.violations
        if show_hints or violation.severity is not Severity.HINT
    ]
    blocks: list[str] = []
    if not statistics:
        blocks.extend(
            _render_violation(v, read_line, style) for v in _group_unclassified(shown)
        )
    lines = ["\n\n".join(blocks)] if blocks else []
    if blocks:
        lines.append("")
    edges = _repeated_edges(shown)
    if edges:
        lines.extend([*edges, ""])
    facades = _facades(shown)
    if facades:
        lines.extend([*facades, ""])
    many_rules = len({violation.code for violation in shown}) > 1
    if shown and (statistics or (len(shown) > _STATISTICS_THRESHOLD and many_rules)):
        lines.extend([_statistics(shown, style), ""])
    if report.scope:
        lines.append(
            f"Scope: {', '.join(report.scope)} ({report.checked_files} analyzed source/test files)"
        )
    lines.append(_summary(report, style, show_hints=show_hints))
    return "\n".join(lines) + "\n"


def _group_unclassified(violations: list[Violation]) -> list[Violation]:
    groups: dict[tuple[Severity, str | None], list[Violation]] = {}
    for violation in violations:
        if violation.code == "SMT305":
            groups.setdefault((violation.severity, violation.hint), []).append(
                violation
            )
    result: list[Violation] = []
    for violation in violations:
        if violation.code != "SMT305":
            result.append(violation)
            continue
        group = groups[(violation.severity, violation.hint)]
        if violation is not group[0]:
            continue
        if len(group) > 1:
            directory = posixpath.commonpath([v.path for v in group if v.path])
            grouped = replace(
                violation,
                path=directory,
                message=f"{len(group)} modules have no architecture classification; --format json lists individual paths",
            )
            result.append(grouped)
        else:
            result.append(violation)
    return result


def _repeated_edges(violations: list[Violation]) -> list[str]:
    """The dependency edges behind several findings, most frequent first.

    81 findings are rarely 81 problems: one facade or one missing allowance
    often explains a dozen of them.
    """
    counts = Counter((v.code, v.edge) for v in violations if v.edge)
    repeated = sorted(
        ((count, code, edge) for (code, edge), count in counts.items() if count > 1),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    if not repeated:
        return []
    width = len(str(repeated[0][0]))
    lines = ['Repeated dependency edges (often one decision or fix each; JSON "edge"):']
    lines.extend(
        f"  {count:>{width}}x {code} {edge}"
        for count, code, edge in repeated[:_TOP_EDGES]
    )
    if len(repeated) > _TOP_EDGES:
        lines.append(f"  … {len(repeated) - _TOP_EDGES} more")
    return lines


def _facades(violations: list[Violation]) -> list[str]:
    """Modules relaying indirect findings: narrowing one addresses all of its findings."""
    groups = [g for g in group_findings(violations) if g["kind"] == "facade"]
    if not groups:
        return []
    width = len(str(groups[0]["count"]))
    lines = ['Indirect findings by the module relaying them (JSON "groups"):']
    lines.extend(
        f"  {g['count']:>{width}}x {g['key']} ({', '.join(g['edges'])})"
        for g in groups[:_TOP_EDGES]
    )
    if len(groups) > _TOP_EDGES:
        lines.append(f"  … {len(groups) - _TOP_EDGES} more")
    return lines


def _statistics(violations: list[Violation], style: _Style) -> str:
    counts = Counter((v.code, v.rule, v.severity) for v in violations)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0][0]))
    width = len(str(ordered[0][1]))
    name_width = max(len(rule) for (_, rule, _), _ in ordered)
    return "\n".join(
        f"{count:>{width}}  {style(code, _BOLD)} {rule:<{name_width}}  "
        + style(f"[{severity.value}]", _COLORS[severity])
        for (code, rule, severity), count in ordered
    )


def _render_violation(
    violation: Violation, read_line: LineReader, style: _Style
) -> str:
    location = violation.path or "<project>"
    if violation.line:
        location += f":{violation.line}"
        if violation.column:
            location += f":{violation.column}"
    severity = style(f"[{violation.severity.value}]", _COLORS[violation.severity])
    header = f"{style(location, _BOLD)}  {style(violation.code, _BOLD)} {violation.rule}  {severity}"
    out = [header, f"  {violation.message}"]
    out.extend(_snippet(violation, read_line, style))
    out.extend(_chain(violation))
    if violation.expected:
        out.extend(_expected(violation, violation.expected))
    if violation.hint:
        out.append(_wrap("Hint: ", violation.hint))
    return "\n".join(out)


def _chain(violation: Violation) -> list[str]:
    links = violation.import_chain
    if not links:
        return []
    connected = all(a.imported == b.importer for a, b in itertools.pairwise(links))
    if connected:
        modules = [links[0].importer, *(link.imported for link in links)]
        return [_wrap("Chain: ", " → ".join(modules))]
    lines = ["  Imports:"]
    for link in links:
        where = f" (line {link.line})" if link.line else ""
        lines.append(f"    {link.importer} → {link.imported}{where}")
    return lines


def _snippet(violation: Violation, read_line: LineReader, style: _Style) -> list[str]:
    if not violation.path or not violation.line:
        return []
    raw = read_line(violation.path, violation.line)
    if not raw.strip():
        return []
    stripped = raw.lstrip()
    indent = len(raw) - len(stripped)
    lines = [f"    {stripped.rstrip()}"]
    if (
        violation.column
        and violation.end_column
        and violation.end_line in (None, violation.line)
    ):
        start = max(violation.column - 1 - indent, 0)
        width = max(violation.end_column - violation.column, 1)
        carets = style("^" * width, _COLORS[violation.severity])
        lines.append("    " + " " * start + carets)
    return lines


def _expected(violation: Violation, expected: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, value in expected.items():
        if key == "may_depend_on" and violation.layer:
            targets = ", ".join(value) if value else "(nothing)"
            lines.append(f"  Allowed: {violation.layer} → {targets}")
        elif key in _MACHINE_ONLY:
            continue
        elif key == "candidates":
            lines.extend(_candidate_lines(value))
        elif key in ("path", "expected_path"):
            lines.append(f"  Expected: {value}")
        else:
            lines.append(
                _wrap(f"Expected {key.replace('_', ' ')}: ", _format_value(value))
            )
    return lines


def _candidate_lines(candidates: list[Mapping[str, Any]]) -> list[str]:
    """``application/commands/ (ChannelCommands, HelpCommand via ...application)``."""
    lines = ["  Candidates:"]
    for candidate in candidates:
        names = ", ".join(candidate.get("imports", ()))
        via = ", ".join(candidate.get("via", ()))
        detail = f"{names} via {via}" if via else names
        lines.append(
            f"    {candidate['source']} ({detail}) → {candidate['test_path']}"
            if detail
            else f"    {candidate['source']} → {candidate['test_path']}"
        )
    return lines


def _format_value(value: object) -> str:
    if isinstance(value, list | tuple):
        return ", ".join(str(v) for v in value) if value else "(none)"
    if isinstance(value, Mapping):
        return "; ".join(f"{k}: {_format_value(v)}" for k, v in value.items())
    return str(value)


def _wrap(label: str, text: str) -> str:
    return textwrap.fill(
        text,
        width=_WIDTH,
        initial_indent=f"  {label}",
        subsequent_indent=" " * (len(label) + 2),
        break_on_hyphens=False,
        break_long_words=False,
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _summary(report: Report, style: _Style, *, show_hints: bool) -> str:
    errors = report.count(Severity.ERROR)
    warnings = report.count(Severity.WARNING)
    hints = report.count(Severity.HINT)
    hint_text = _plural(hints, "hint")
    if hints and not show_hints:
        hint_text += " (use --show-hints)"
    tail: list[str] = []
    if report.suppressed:
        tail.append(f"{report.suppressed} suppressed")
    if report.in_debt:
        tail.append(f"{report.in_debt} in debt")
    tail.append(_plural(report.modules, "module"))

    if errors == 0 and warnings == 0:
        checks = " ".join(style(f"✓ {c.value}", _GREEN) for c in report.categories)
        parts = [checks or style("✓ nothing to check", _GREEN)]
        if hints:
            parts.append(hint_text)
        return " · ".join([*parts, *tail])
    mark = style("✗", _RED) if report.failed else style("!", _YELLOW)
    counts = [_plural(errors, "error"), _plural(warnings, "warning"), hint_text]
    return f"{mark} " + " · ".join([*counts, *tail])
