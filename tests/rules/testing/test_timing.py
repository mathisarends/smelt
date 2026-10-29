from __future__ import annotations

from typing import TYPE_CHECKING

from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path


def test_deadline_review_reports_unbounded_process_and_shared_clock(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {review_test_deadlines: true}",
            "app/__init__.py": "",
            "tests/test_imports.py": "import subprocess\nimport asyncio\ndef test_import():\n    subprocess.run(command, check=True)\n    loop = asyncio.get_running_loop()\n    loop.time = lambda: 0.0",
        },
    )
    found = violations(tmp_path)
    assert len(found) == 2
    assert {finding.code for finding in found} == {"SMT426"}
    assert {finding.expected["kind"] for finding in found if finding.expected} == {
        "unbounded-wait",
        "scheduler-clock",
    }
    assert all(finding.severity.value == "warning" for finding in found)
