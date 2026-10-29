from __future__ import annotations

from typing import TYPE_CHECKING

from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


def test_case_loop_review_remains_an_opt_in_hint(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {review_case_loops: true}",
            "app/__init__.py": "",
            "tests/test_selection.py": "def test_selection():\n    for path in ['app/a.py', 'app/b.py']:\n        selected = select(path)\n        assert selected == ['app']",
        },
    )
    [finding] = violations(tmp_path)
    assert finding.code == "SMT427"
    assert finding.severity.value == "hint"
    assert finding.line == 2
    assert finding.expected is not None
    assert finding.expected["cases"] == 2
