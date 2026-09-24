from __future__ import annotations

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


class TestSummary:
    def test_clean_report_lists_the_categories(self, clean_report: Report) -> None:
        assert render_text(clean_report, read_line) == (
            "✓ dependencies ✓ structure · 4 modules\n"
        )

    def test_clean_report_with_color(self, clean_report: Report) -> None:
        assert render_text(clean_report, read_line, color=True) == (
            "\x1b[32m✓ dependencies\x1b[0m \x1b[32m✓ structure\x1b[0m · 4 modules\n"
        )
