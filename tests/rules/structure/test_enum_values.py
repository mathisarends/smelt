from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import check, violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("SMT318",))
SOURCE = """from enum import StrEnum

class MouseEventKind(StrEnum):
    MOVED = "mouseMoved"
    PRESSED = "mousePressed"
    RELEASED = "mouseReleased"
    WHEEL = "mouseWheel"

async def dispatch_wheel(cdp, point, *, delta_x: float, delta_y: float) -> None:
    await cdp.input.dispatch_mouse_event(
        type=MouseEventKind.WHEEL.value, x=point.x, y=point.y, delta_x=delta_x, delta_y=delta_y
    )
"""


def _project(tmp_path: Path, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yaml": config, "app/__init__.py": "", "app/mouse.py": SOURCE},
    )


def test_reports_the_original_dispatch_example_without_changing_source(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    before = (root / "app/mouse.py").read_bytes()
    [finding] = violations(root, OPTIONS)

    assert finding.code == "SMT318"
    assert finding.severity is Severity.HINT
    assert (finding.path, finding.line, finding.column) == ("app/mouse.py", 11, 14)
    assert (finding.end_line, finding.end_column) == (11, 40)
    assert finding.expected == {
        "enum": "app.mouse.MouseEventKind",
        "member": "WHEEL",
        "replacement": "MouseEventKind.WHEEL",
        "automatic_rewrite": False,
    }
    assert finding.hint is not None
    assert "exact str" in finding.hint
    assert (root / "app/mouse.py").read_bytes() == before


def test_rule_is_off_by_default(tmp_path: Path) -> None:
    assert violations(_project(tmp_path)) == []


@pytest.mark.parametrize("name", ["SMT318", "redundant-str-enum-value"])
def test_explicit_rule_setting_enables_the_hint(tmp_path: Path, name: str) -> None:
    [finding] = violations(_project(tmp_path, CONFIG + f"rules: {{{name}: hint}}\n"))
    assert finding.code == "SMT318"


@pytest.mark.parametrize(
    ("config", "options"),
    [
        (CONFIG + "rules: {SMT318: off}\n", OPTIONS),
        (CONFIG, CheckOptions(select=("SMT318",), ignore=("SMT318",))),
    ],
)
def test_explicit_disable_and_ignore_are_respected(
    tmp_path: Path, config: str, options: CheckOptions
) -> None:
    assert violations(_project(tmp_path, config), options) == []


def test_exact_string_boundary_can_be_suppressed_with_a_reason(tmp_path: Path) -> None:
    root = _project(tmp_path, CONFIG + "rules: {SMT318: error}\n")
    assert check(root).report.failed
    source = root / "app/mouse.py"
    source.write_text(
        SOURCE.replace(
            "delta_y=delta_y\n",
            "delta_y=delta_y  # smelt: ignore[SMT318] -- API requires an exact str\n",
        ),
        encoding="utf-8",
    )
    outcome = check(root)
    assert outcome.report.violations == []
    assert outcome.report.suppressed == 1
