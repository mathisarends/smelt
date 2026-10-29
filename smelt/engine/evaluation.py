"""Explicit version comparisons and optional pytest runs, outside normal checks."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

_LAUNCHER = """import sys
from pathlib import Path
root = Path(sys.argv.pop(1)).resolve()
expected = root / 'smelt' / '__init__.py'
if not expected.is_file():
    raise SystemExit(f'missing Smelt package: {expected}')
sys.path.insert(0, str(root))
import smelt
if Path(smelt.__file__).resolve() != expected.resolve():
    raise SystemExit('unexpected Smelt import origin')
from smelt.cli import main
raise SystemExit(main(sys.argv[1:]))
"""


@dataclass(frozen=True)
class EvaluationOptions:
    config: Path
    baseline: Path
    candidate: Path
    reports: Path
    python: Path = field(default_factory=lambda: Path(sys.executable))
    select: tuple[str, ...] = ("SMT42",)
    timeout: float = 60
    pytest_targets: tuple[str, ...] = ()
    test_python: Path | None = None
    pytest_args: tuple[str, ...] = ()
    python_paths: tuple[Path, ...] = ()
    source_revision: str | None = None


def _identity(finding: dict[str, Any]) -> str:
    expected = finding.get("expected") or {}
    return json.dumps(
        [
            finding.get(key)
            for key in (
                "code",
                "path",
                "line",
                "column",
                "source_module",
                "target_module",
            )
        ]
        + [expected.get("test"), expected.get("operation")],
        sort_keys=True,
    )


def compare_reports(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare identical sources/policies; retain multiplicity and changed payloads."""
    remaining: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    for finding in before["violations"]:
        remaining[_identity(finding)].append(finding)
    added, updated = [], []
    for finding in after["violations"]:
        previous = remaining[_identity(finding)]
        if not previous:
            added.append(finding)
            continue
        old = previous.popleft()
        if old != finding:
            updated.append({"before": old, "after": finding})
    return {
        "before": dict(
            sorted(Counter(v["code"] for v in before["violations"]).items())
        ),
        "after": dict(sorted(Counter(v["code"] for v in after["violations"]).items())),
        "added": added,
        "removed": [finding for key in sorted(remaining) for finding in remaining[key]],
        "updated": updated,
        "interpretation": "Recognition changes under the same input; counts are not confirmed defects or test-effectiveness scores.",
    }


def run_process(
    args: list[str], cwd: Path, timeout: float, *, env: dict[str, str] | None = None
) -> dict[str, Any]:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    start = perf_counter()
    result: dict[str, Any] = {"args": args, "cwd": str(cwd), "timeout_seconds": timeout}
    try:
        # Argument vectors come from this explicit evaluation command, without a shell.
        process = subprocess.run(  # noqa: S603
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        result.update(
            exit=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
            error=None,
        )
    except subprocess.TimeoutExpired as exc:
        result.update(
            exit=None,
            stdout=_decoded(exc.stdout),
            stderr=_decoded(exc.stderr),
            error="process-timeout",
        )
    except OSError as exc:
        result.update(
            exit=None, stdout="", stderr=str(exc), error="process-start-failed"
        )
    result["duration_seconds"] = round(perf_counter() - start, 3)
    return result


def _decoded(value: str | bytes | None) -> str:
    return (
        value.decode("utf-8", errors="replace")
        if isinstance(value, bytes)
        else value or ""
    )


def _report(result: dict[str, Any]) -> dict[str, Any] | None:
    try:
        value = json.loads(result["stdout"])
    except (ValueError, TypeError):
        result["error"] = result["error"] or "invalid-json"
        return None
    if (
        result["exit"] not in (0, 1)
        or not isinstance(value, dict)
        or not isinstance(value.get("status"), str)
        or value.get("status") not in {"passed", "failed"}
        or not isinstance(value.get("violations"), list)
    ):
        result["error"] = result["error"] or "analysis-failed"
        return None
    for finding in value["violations"]:
        if (
            not isinstance(finding, dict)
            or not isinstance(finding.get("code"), str)
            or (
                finding.get("expected") is not None
                and not isinstance(finding["expected"], dict)
            )
        ):
            result["error"] = "invalid-report"
            return None
    return value


def evaluate(options: EvaluationOptions) -> dict[str, Any]:
    """Never fix sources, alter policy, install packages, suppress findings or record debt."""
    if not math.isfinite(options.timeout) or options.timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    config = options.config.resolve()
    policy_hash = hashlib.sha256(config.read_bytes()).hexdigest()
    output = options.reports.resolve()
    output.mkdir(parents=True, exist_ok=True)
    checks: dict[str, Any] = {}
    reports: dict[str, dict[str, Any]] = {}
    for label, package in (
        ("baseline", options.baseline),
        ("candidate", options.candidate),
    ):
        command = [
            str(options.python),
            "-c",
            _LAUNCHER,
            str(package.resolve()),
            "check",
            "--config",
            str(config),
            "--format",
            "json",
        ]
        if options.select:
            command.extend(["--select", ",".join(options.select)])
        result = run_process(command, config.parent, options.timeout)
        report = _report(result)
        checks[label] = result
        (output / f"{label}.stdout.txt").write_text(result["stdout"], encoding="utf-8")
        (output / f"{label}.stderr.txt").write_text(result["stderr"], encoding="utf-8")
        if report is not None:
            reports[label] = report
        payload = (
            report
            if report is not None
            else {
                "schema_version": 1,
                "status": "error",
                "error": {"kind": result["error"], "exit": result["exit"]},
            }
        )
        (output / f"{label}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
    try:
        policy_unchanged = (
            hashlib.sha256(config.read_bytes()).hexdigest() == policy_hash
        )
    except OSError:
        policy_unchanged = False
    analysis_ok = len(reports) == len(checks) and policy_unchanged
    tests: dict[str, Any] = {"executed": False}
    # Do not execute test code after invalid/incomplete analysis input.
    if analysis_ok and options.pytest_targets:
        environment = dict(os.environ)
        if options.python_paths:
            environment["PYTHONPATH"] = os.pathsep.join(
                [
                    *(str(path.resolve()) for path in options.python_paths),
                    environment.get("PYTHONPATH", ""),
                ]
            )
        command = [
            str(options.test_python or options.python),
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *options.pytest_args,
            *options.pytest_targets,
            "--basetemp",
            str(output / "pytest-temp"),
        ]
        tests = {
            "executed": True,
            **run_process(command, config.parent, options.timeout, env=environment),
        }
        (output / "pytest.stdout.txt").write_text(tests["stdout"], encoding="utf-8")
        (output / "pytest.stderr.txt").write_text(tests["stderr"], encoding="utf-8")
    if tests.get("executed") and tests["exit"] not in (0, 1) and not tests.get("error"):
        tests["error"] = "pytest-incomplete"
    if not tests.get("executed"):
        for name in ("pytest.stdout.txt", "pytest.stderr.txt"):
            (output / name).write_text("", encoding="utf-8")
    failed_to_run = not analysis_ok or bool(tests.get("error"))
    exit_code = (
        2 if failed_to_run else 1 if tests.get("executed") and tests["exit"] != 0 else 0
    )
    document = {
        "schema_version": 1,
        "status": "error"
        if failed_to_run
        else "tests-failed"
        if exit_code
        else "complete",
        "exit_code": exit_code,
        "config": str(config),
        "config_sha256": policy_hash,
        "policy_unchanged": policy_unchanged,
        "source_revision": options.source_revision,
        "analysis": checks,
        "comparison": compare_reports(reports["baseline"], reports["candidate"])
        if analysis_ok
        else None,
        "pytest": tests,
        "limits": [
            "Static findings require semantic review.",
            "Only explicitly requested pytest targets execute; no mutation testing is implied.",
            "Use a fixed snapshot/revision for source stability; config bytes are checked for concurrent changes.",
        ],
    }
    (output / "evaluation.json").write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    return document
