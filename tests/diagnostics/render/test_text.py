from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from smelt.diagnostics.render.text import render_text
from tests.diagnostics.render.conftest import read_line

if TYPE_CHECKING:
    from smelt.diagnostics.report import Report


class TestViolations:
    def test_full_report(self, report: Report) -> None:
        assert render_text(report, read_line) == (
            "gw/features/voice/domain/calls.py:1:1  SMT101 layer-boundary  [error]\n"
            "  domain must not import infrastructure\n"
            "    from gw.features.voice.infra import sql\n"
            "    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
            "  Chain: gw.features.voice.domain.calls → gw.features.voice.infra.sql\n"
            "  Allowed: domain → (nothing)\n"
            "  Hint: Depend on a port instead.\n"
            "\n"
            "gw/features/voice/infra/sql.py:1:7  SMT303 role-file  [warning]\n"
            "  port Sessions must be defined in voice/application/ports.py\n"
            "    class SqlSessions:\n"
            "          ^^^^^^^^^^^\n"
            "  Expected: voice/application/ports.py\n"
            "  Hint: Move Sessions to voice/application/ports.py and update its imports.\n"
            "\n"
            "✗ 1 error · 1 warning · 1 hint (use --show-hints) · 1 suppressed · "
            "2 in debt · 4 modules\n"
        )

    def test_hints_are_shown_on_request(self, report: Report) -> None:
        out = render_text(report, read_line, show_hints=True)

        assert "gw/features/voice/infra/  SMT305 crowded-package  [hint]" in out
        assert out.endswith(
            "✗ 1 error · 1 warning · 1 hint · 1 suppressed · 2 in debt · 4 modules\n"
        )

    def test_color_wraps_severity_and_carets(self, report: Report) -> None:
        out = render_text(report, read_line, color=True)

        assert "\x1b[31m[error]\x1b[0m" in out
        assert "\x1b[31m^^^^" in out


class TestRepeatedEdges:
    def test_edges_behind_several_findings_are_counted(self, report: Report) -> None:
        first = report.violations[0]
        report.violations = [
            *(replace(first, path=f"gw/calls_{n}.py") for n in range(3)),
            replace(first, code="SMT102", edge="voice.domain -> billing.domain"),
            replace(first, code="SMT102", edge="voice.domain -> billing.domain"),
            replace(first, code="SMT102", edge="voice.domain -> chat.domain"),
        ]

        out = render_text(report, read_line)

        assert (
            'Repeated dependency edges (often one decision or fix each; JSON "edge"):\n'
            "  3x SMT101 voice.domain -> voice.infra\n"
            "  2x SMT102 voice.domain -> billing.domain\n"
            "\n"
        ) in out
        assert "chat.domain" not in out.split("Repeated")[1]

    def test_single_findings_have_no_edge_block(self, report: Report) -> None:
        assert "Repeated dependency edges" not in render_text(report, read_line)

    def test_more_than_five_edges_are_cut(self, report: Report) -> None:
        first = report.violations[0]
        report.violations = [
            replace(first, edge=f"a{n} -> b") for n in range(7) for _ in range(2)
        ]

        out = render_text(report, read_line)

        assert "  2x SMT101 a4 -> b\n  … 2 more\n" in out


class TestStatistics:
    def test_statistics_replace_the_listing(self, report: Report) -> None:
        out = render_text(report, read_line, statistics=True, show_hints=True)

        assert out == (
            "1  SMT101 layer-boundary   [error]\n"
            "1  SMT303 role-file        [warning]\n"
            "1  SMT305 crowded-package  [hint]\n"
            "\n"
            "✗ 1 error · 1 warning · 1 hint · 1 suppressed · 2 in debt · 4 modules\n"
        )

    def test_long_listing_ends_with_statistics(self, report: Report) -> None:
        many = replace(report, violations=report.violations * 6)

        out = render_text(many, read_line)

        assert out.endswith(
            "6  SMT101 layer-boundary  [error]\n"
            "6  SMT303 role-file       [warning]\n"
            "\n"
            "✗ 6 errors · 6 warnings · 6 hints (use --show-hints) · 1 suppressed · "
            "2 in debt · 4 modules\n"
        )

    def test_short_listing_has_no_statistics(self, report: Report) -> None:
        out = render_text(report, read_line)

        assert "\n1  SMT101 layer-boundary" not in out


class TestSummary:
    def test_clean_report_lists_the_categories(self, clean_report: Report) -> None:
        assert render_text(clean_report, read_line) == (
            "✓ dependencies ✓ structure · 4 modules\n"
        )

    def test_clean_report_with_color(self, clean_report: Report) -> None:
        assert render_text(clean_report, read_line, color=True) == (
            "\x1b[32m✓ dependencies\x1b[0m \x1b[32m✓ structure\x1b[0m · 4 modules\n"
        )
