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


def test_async_exit_wait_needs_its_own_guard_after_bounded_communication(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}\ntests: {review_test_deadlines: true}",
            "app/__init__.py": "",
            "tests/conftest.py": "import asyncio\nimport pytest\n@pytest.fixture\nasync def process():\n    child = await asyncio.create_subprocess_exec('tool')\n    yield child",
            "tests/test_process.py": """
                import asyncio
                async def test_exit(process):
                    await asyncio.wait_for(process.communicate(), timeout=1)
                    process.kill()
                    await process.wait()
                async def test_bounded_exit(process):
                    async with asyncio.timeout(1):
                        await process.wait()
            """,
        },
    )
    [finding] = violations(tmp_path)
    assert finding.code == "SMT426"
    assert finding.line == 5
    assert finding.expected == {
        "test": "test_exit",
        "kind": "unbounded-wait",
        "operation": "asyncio.subprocess.Process.wait",
        "provenance": "fixture-inferred",
    }
