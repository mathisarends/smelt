"""Measure real CLI commit checks on a disposable repository, including startup."""

from __future__ import annotations

import argparse
import json
import os
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


def benchmark(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    package = root / "app"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (root / "smelt.yaml").write_text(
        "version: 1\nproject: {root_packages: [app]}\nstructure:\n  contracts:\n"
        "    api: {match: app, rules: {internal-public-member: warning}}\n",
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
            f"class Service{index}:\n    def run(self):\n{calls}{methods}\nService{index}().run()\n",
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
    git(root, "add", "app/service_0.py")
    results: dict[str, Any] = {
        "modules": args.modules,
        "methods_per_module": args.methods,
        "repeats": args.repeats,
        "seconds": {},
        "samples_seconds": {},
    }
    times = results["seconds"]
    if args.baseline:
        samples = [
            invoke(root, args.baseline, "--changed") for _ in range(args.repeats)
        ]
        times["baseline_changed"] = statistics.median(t for t, _ in samples)
        results["samples_seconds"]["baseline_changed"] = [t for t, _ in samples]
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
    changed.write_text(
        changed.read_text(encoding="utf-8").replace(
            "new_public_helper", "another_public_helper"
        ),
        encoding="utf-8",
    )
    git(root, "add", "app/service_0.py")
    elapsed, edited = invoke(root, args.candidate, "--staged")
    times["staged_one_file_edit"] = elapsed
    _, verified = invoke(root, args.candidate, "--staged", "--no-cache")
    if edited != verified:
        raise RuntimeError("incremental edit changed the report")
    results["introduced_findings"] = len(edited["violations"])
    results["reports_match"] = True
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument("--baseline", type=Path)
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
