import itertools
import textwrap
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from smelt.diagnostics.violation import Severity, Violation

if TYPE_CHECKING:
    from smelt.diagnostics.report import LineReader, Report

_WIDTH = 88
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
) -> str:
    style = _Style(color)
    blocks: list[str] = []
    for violation in report.violations:
        if violation.severity is Severity.HINT and not show_hints:
            continue
        blocks.append(_render_violation(violation, read_line, style))
    lines = ["\n\n".join(blocks)] if blocks else []
    if blocks:
        lines.append("")
    lines.append(_summary(report, style, show_hints=show_hints))
    return "\n".join(lines) + "\n"


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
    if violation.fix is not None or violation.fixable:
        out.append(style("  Fixable with `smelt fix`", _DIM))
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
        elif key == "cycle":
            continue
        elif key in ("path", "expected_path"):
            lines.append(f"  Expected: {value}")
        else:
            lines.append(
                _wrap(f"Expected {key.replace('_', ' ')}: ", _format_value(value))
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
    if report.baselined:
        tail.append(f"{report.baselined} baselined")
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
