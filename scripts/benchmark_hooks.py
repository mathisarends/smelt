"""Measure complete hook-runner invocations on independent staged repositories."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml
from benchmark_checks import create_project, git, invoke, summarize


def prepare(root: Path, analyzer: Path, args: argparse.Namespace) -> Path:
    root.mkdir()
    changed = create_project(args, root)
    command = [str(analyzer)] if analyzer.is_file() else [sys.executable, "-m", "smelt"]
    hook = {
        "id": "smelt",
        "name": "smelt",
        "entry": shlex.join(
            [*command, "check", "--staged", "--format", "json", "--fail-on", "warning"]
        ),
        "language": "system",
        "pass_filenames": False,
        "always_run": True,
    }
    config = root / ".pre-commit-config.yaml"
    config.write_text(yaml.safe_dump({"repos": [{"repo": "local", "hooks": [hook]}]}))
    git(root, "add", config.name)
    # Commit only hook setup, preserving the intentionally staged source edit.
    git(root, "commit", "-qm", "configure hook", "--only", config.name)
    return changed


def hook_check(
    root: Path, analyzer: Path, runner: list[str], *, all_files: bool
) -> tuple[float, dict[str, Any]]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if analyzer.is_file():
        env.pop("PYTHONPATH", None)
        env["SMELT_DAEMON"] = "1"
    else:
        env["PYTHONPATH"] = str(analyzer)
    started = time.perf_counter()
    completed = subprocess.run(  # noqa: S603 - explicit maintainer-selected runner
        [
            *runner,
            "run",
            "smelt",
            *(["--all-files"] if all_files else []),
            "--show-diff-on-failure",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode not in {0, 1} or "{" not in completed.stdout:
        raise RuntimeError(f"hook failed: {completed.stderr}\n{completed.stdout}")
    report, _ = json.JSONDecoder().raw_decode(
        completed.stdout[completed.stdout.index("{") :]
    )
    if report.get("status") not in {"passed", "failed"}:
        raise RuntimeError(f"check failed: {report}")
    return elapsed, report


def benchmark(args: argparse.Namespace, parent: Path) -> dict[str, Any]:
    runner = [str(args.runner)] if args.runner else [sys.executable, "-m", "pre_commit"]
    runner_version = subprocess.run(  # noqa: S603 - explicit runner selected by maintainer
        [*runner, "--version"], capture_output=True, text=True, check=True, timeout=30
    ).stdout.strip()
    analyzers = {"baseline": args.baseline, "candidate": args.candidate}
    roots = {name: parent / name for name in analyzers}
    changed = {
        name: prepare(roots[name], analyzer, args)
        for name, analyzer in analyzers.items()
    }
    expected = {
        name: invoke(roots[name], analyzer, "--staged", "--no-cache")[1]
        for name, analyzer in analyzers.items()
    }
    first: dict[str, float] = {}
    for name, analyzer in analyzers.items():
        first[name], report = hook_check(
            roots[name], analyzer, runner, all_files=args.all_files
        )
        if report != expected[name]:
            raise RuntimeError("first hook report differs from independent analysis")
    samples: dict[str, list[float]] = {
        f"{name}_{scenario}": []
        for scenario in ("retry", "python_edit")
        for name in analyzers
    }
    for scenario in ("retry", "python_edit"):
        previous = "new_public_helper"
        for index in range(args.repeats):
            if scenario == "python_edit":
                current = f"edited_public_helper_{index}"
                for name, path in changed.items():
                    path.write_text(path.read_text().replace(previous, current))
                    git(roots[name], "add", path.relative_to(roots[name]).as_posix())
                previous = current
            order = ["baseline", "candidate"]
            if index % 2:
                order.reverse()
            reports = {}
            for name in order:
                elapsed, reports[name] = hook_check(
                    roots[name], analyzers[name], runner, all_files=args.all_files
                )
                samples[f"{name}_{scenario}"].append(elapsed)
            for name, report in reports.items():
                reference = (
                    expected[name]
                    if scenario == "retry"
                    else invoke(roots[name], analyzers[name], "--staged", "--no-cache")[
                        1
                    ]
                )
                if report != reference:
                    raise RuntimeError("hook report differs from independent analysis")
    return {
        "modules": args.modules,
        "methods_per_module": args.methods,
        "repeats": args.repeats,
        "policy": args.policy,
        "runner": runner_version,
        "all_files": args.all_files,
        "environment": {"platform": platform.platform(), "python": sys.version},
        "execution_order": "Alternating order on separate repositories and caches.",
        "first_hook_seconds": first,
        "samples_seconds": samples,
        "distributions_seconds": {
            name: summarize(values) for name, values in samples.items()
        },
        "reports_match": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--runner", type=Path, help="Optional native runner, e.g. prek")
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="Include runner-wide file classification",
    )
    parser.add_argument("--modules", type=int, default=250)
    parser.add_argument("--methods", type=int, default=12)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument(
        "--policy", choices=["members", "transitive"], default="members"
    )
    args = parser.parse_args()
    args.baseline, args.candidate = args.baseline.resolve(), args.candidate.resolve()
    args.runner = args.runner.resolve() if args.runner else None
    if min(args.modules, args.methods, args.repeats) < 1:
        parser.error("modules, methods and repeats must be positive")
    with tempfile.TemporaryDirectory(prefix="smelt-hook-benchmark-") as temporary:
        result = benchmark(args, Path(temporary))
    sys.stdout.write(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
