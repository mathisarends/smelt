from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from smelt.engine.evaluation import (
    EvaluationOptions,
    _compare_reports,  # smelt: ignore[SMT420] -- Exercise the private analysis primitive and its safety boundaries directly.
    _run_process,  # smelt: ignore[SMT420] -- Exercise the private analysis primitive and its safety boundaries directly.
    evaluate,
)
from tests.helpers import write_project


def test_comparison_retains_multiplicity_and_changed_evidence() -> None:
    original: dict[str, Any] = {
        "code": "SMT422",
        "path": "tests/test_run.py",
        "line": 3,
        "expected": {"operation": "unittest.mock.patch"},
    }
    revised = {**original, "expected": {**original["expected"], "provenance": "import"}}
    result = _compare_reports(
        {"violations": [original, original]}, {"violations": [revised]}
    )
    assert result["before"] == {"SMT422": 2}
    assert result["after"] == {"SMT422": 1}
    assert result["removed"] == [original]
    assert result["added"] == []
    assert result["updated"] == [{"before": original, "after": revised}]


def test_execution_deadline_is_independent_of_the_child_clock(tmp_path: Path) -> None:
    result = _run_process(
        [
            sys.executable,
            "-c",
            "import time; time.monotonic = lambda: 0; time.sleep(20)",
        ],
        tmp_path,
        0.05,
    )
    assert result["error"] == "process-timeout"
    assert result["exit"] is None


@pytest.mark.parametrize(
    ("test_source", "status", "exit_code"),
    [
        ("def test_run():\n    assert 1 == 1", "complete", 0),
        ("def test_run():\n    assert 1 == 2", "tests-failed", 1),
        ("raise RuntimeError('collection failure')", "error", 2),
    ],
)
def test_real_comparison_and_explicit_pytest_targets(
    tmp_path: Path, test_source: str, status: str, exit_code: int
) -> None:
    project = write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject:\n  root_packages:\n  - app\nrules:\n  test-without-outcome-check: warning\n",
            "app/__init__.py": "",
            "tests/test_run.py": test_source,
        },
    )
    package = Path(__file__).resolve().parents[2]
    result = evaluate(
        EvaluationOptions(
            config=project / "smelt.yaml",
            baseline=package,
            candidate=package,
            reports=tmp_path / "reports",
            pytest_targets=("tests/test_run.py",),
        )
    )
    assert result["status"] == status
    assert result["policy_unchanged"]
    assert result["comparison"]["added"] == result["comparison"]["removed"] == []
    assert result["pytest"]["executed"]
    assert result["exit_code"] == exit_code
    assert (
        json.loads((tmp_path / "reports/evaluation.json").read_text())["exit_code"]
        == exit_code
    )


def test_invalid_baseline_still_runs_candidate_and_never_executes_tests(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}",
            "app/__init__.py": "",
            "broken/smelt/__init__.py": "",
            "broken/smelt/cli.py": "def main(args):\n    print('invalid json')\n    return 0",
            "tests/test_run.py": "raise RuntimeError('must not execute')",
        },
    )
    result = evaluate(
        EvaluationOptions(
            config=tmp_path / "smelt.yaml",
            baseline=tmp_path / "broken",
            candidate=Path(__file__).resolve().parents[2],
            reports=tmp_path / "reports",
            pytest_targets=("tests/test_run.py",),
        )
    )
    assert result["status"] == "error"
    assert result["analysis"]["baseline"]["error"] == "invalid-json"
    assert result["analysis"]["candidate"]["exit"] == 0
    assert result["pytest"] == {"executed": False}
    assert result["comparison"] is None


def test_missing_baseline_cannot_fall_back_to_the_installed_candidate(
    tmp_path: Path,
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yaml": "version: 1\nproject: {root_packages: [app]}",
            "app/__init__.py": "",
        },
    )
    result = evaluate(
        EvaluationOptions(
            config=tmp_path / "smelt.yaml",
            baseline=tmp_path / "missing",
            candidate=Path(__file__).resolve().parents[2],
            reports=tmp_path / "reports",
        )
    )
    assert result["status"] == "error"
    assert "missing Smelt package" in result["analysis"]["baseline"]["stderr"]
    assert result["analysis"]["candidate"]["exit"] == 0
    assert (
        json.loads((tmp_path / "reports/baseline.json").read_text())["status"]
        == "error"
    )
