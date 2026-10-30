"""Measure real CLI commit checks on a disposable repository, including startup."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        [  # noqa: S607 - use Git from PATH, like the CLI under measurement
            "git",
            "-c",
            "user.name=benchmark",
            "-c",
            "user.email=benchmark@example.invalid",
            *args,
        ],
        cwd=root,
        capture_output=True,
        check=True,
    )


def invoke(root: Path, analyzer: Path, *args: str) -> tuple[float, dict[str, Any]]:
    env = {**os.environ, "PYTHONPATH": str(analyzer), "PYTHONIOENCODING": "utf-8"}
    started = time.perf_counter()
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "smelt",
            "check",
            *args,
            "--format",
            "json",
            "--fail-on",
            "warning",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    elapsed = time.perf_counter() - started
    report = json.loads(completed.stdout)
    if completed.returncode not in {0, 1} or completed.stderr:
        raise RuntimeError(f"benchmark check failed: {completed.stderr or report}")
    return elapsed, report


def identity(report: dict[str, Any]) -> list[dict[str, Any]]:
    # Repair wording may improve between analyzers; the decisions must agree.
    return [
        {key: value for key, value in finding.items() if key != "hint"}
        for finding in report["violations"]
    ]


def summarize(samples: list[float]) -> dict[str, float]:
    """Use a nearest-rank p95; retain raw samples rather than implying precision."""
    return {
        "median": statistics.median(samples),
        "p95": sorted(samples)[math.ceil(0.95 * len(samples)) - 1],
        "max": max(samples),
    }


def create_project(args: argparse.Namespace, root: Path) -> Path:
    package = root / "app"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    architecture = args.policy != "members"
    architecture_config = ""
    if architecture:
        (package / "domain").mkdir()
        (package / "domain/__init__.py").write_text("", encoding="utf-8")
        package /= "application"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        architecture_config = "architecture:\n  layers:\n    application: {path: application, may_depend_on: [domain]}\n    domain: {path: domain, may_depend_on: []}\n"
    (root / "smelt.yaml").write_text(
        "version: 1\nproject: {root_packages: [app]}\nstructure:\n  contracts:\n"
        "    api: {match: app, rules: {internal-public-member: warning}}\n"
        + architecture_config,
        encoding="utf-8",
    )
    for index in range(args.modules):
        methods = "".join(
            f"    def helper_{index}_{n}(self):\n        return {n}\n"
            for n in range(args.methods)
        )
        calls = "".join(
            f"        self.helper_{index}_{n}()\n" for n in range(args.methods)
        )
        (package / f"service_{index}.py").write_text(
            ("from app import domain\n" if architecture else "")
            + f"class Service{index}:\n    def run(self):\n{calls}{methods}\nService{index}().run()\n",
            encoding="utf-8",
        )
    git(root, "init", "-q", "-b", "main")
    git(root, "add", ".")
    git(root, "commit", "-qm", "baseline")
    changed = package / "service_0.py"
    changed.write_text(
        changed.read_text(encoding="utf-8") + "\ndef new_public_helper(): pass\n",
        encoding="utf-8",
    )
    git(root, "add", changed.relative_to(root).as_posix())
    return changed


def benchmark(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    changed = create_project(args, root)
    results: dict[str, Any] = {
        "modules": args.modules,
        "methods_per_module": args.methods,
        "repeats": args.repeats,
        "policy": args.policy,
        "environment": {"platform": platform.platform(), "python": sys.version},
        "baseline_mode": args.baseline_mode if args.baseline else None,
        "seconds": {},
        "samples_seconds": {},
    }
    times = results["seconds"]
    if args.baseline:
        first, _ = invoke(root, args.baseline, f"--{args.baseline_mode}")
        times["baseline_first_check"] = first
        samples = [
            invoke(root, args.baseline, f"--{args.baseline_mode}")
            for _ in range(args.repeats)
        ]
        label = f"baseline_{args.baseline_mode}"
        times[label] = statistics.median(t for t, _ in samples)
        results["samples_seconds"][label] = [t for t, _ in samples]
        reference = identity(samples[-1][1])
    else:
        reference = None
    elapsed, uncached = invoke(root, args.candidate, "--staged", "--no-cache")
    times["staged_uncached"] = elapsed
    if reference is not None and reference != identity(uncached):
        raise RuntimeError("baseline and candidate findings differ")
    elapsed, cold = invoke(root, args.candidate, "--staged")
    times["staged_cold_cache"] = elapsed
    if cold != uncached:
        raise RuntimeError("cold cache changed the report")
    samples = [invoke(root, args.candidate, "--staged") for _ in range(args.repeats)]
    if any(report != uncached for _, report in samples):
        raise RuntimeError("warm cache changed the report")
    times["staged_warm_median"] = statistics.median(t for t, _ in samples)
    times["staged_warm_max"] = max(t for t, _ in samples)
    results["samples_seconds"]["staged_warm"] = [t for t, _ in samples]
    edit_samples, baseline_edits, edited = benchmark_edits(root, changed, args)
    times["staged_one_file_edit"] = statistics.median(edit_samples)
    results["samples_seconds"]["staged_one_file_edit"] = edit_samples
    if baseline_edits:
        label = f"baseline_{args.baseline_mode}_one_file_edit"
        times[label] = statistics.median(baseline_edits)
        results["samples_seconds"][label] = baseline_edits
    results["distributions_seconds"] = {
        name: summarize(values) for name, values in results["samples_seconds"].items()
    }
    results["introduced_findings"] = len(edited["violations"])
    results["reports_match"] = True
    return results


def benchmark_edits(
    root: Path, changed: Path, args: argparse.Namespace
) -> tuple[list[float], list[float], dict[str, Any]]:
    edit_samples: list[float] = []
    baseline_samples: list[float] = []
    previous_name = "new_public_helper"
    for index in range(args.repeats):
        name = f"another_public_helper_{index}"
        changed.write_text(
            changed.read_text(encoding="utf-8").replace(previous_name, name),
            encoding="utf-8",
        )
        previous_name = name
        git(root, "add", changed.relative_to(root).as_posix())
        if args.baseline:
            elapsed, before = invoke(root, args.baseline, f"--{args.baseline_mode}")
            baseline_samples.append(elapsed)
        elapsed, edited = invoke(root, args.candidate, "--staged")
        edit_samples.append(elapsed)
        _, verified = invoke(root, args.candidate, "--staged", "--no-cache")
        if edited != verified:
            raise RuntimeError("incremental edit changed the report")
        if args.baseline and identity(before) != identity(edited):
            raise RuntimeError("baseline and incremental decisions differ")
    return edit_samples, baseline_samples, edited


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--baseline-mode", choices=["changed", "staged"], default="changed"
    )
    parser.add_argument(
        "--policy", choices=["members", "architecture"], default="members"
    )
    parser.add_argument("--modules", type=int, default=250)
    parser.add_argument("--methods", type=int, default=12)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    if min(args.modules, args.methods, args.repeats) < 1:
        parser.error("modules, methods and repeats must be positive")
    args.candidate = args.candidate.resolve()
    args.baseline = args.baseline.resolve() if args.baseline else None
    with tempfile.TemporaryDirectory(prefix="smelt-benchmark-") as tmp:
        result = benchmark(args, Path(tmp))
    sys.stdout.write(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
