"""Compare hook latency with origin/main without changing the caller's index.

Uses the current native binary with two isolated Python source snapshots. Native
sources and dependency locks must match the base; use installed-wheel benchmarks
instead when comparing native code or dependencies. Timings include a fresh CLI
process, Git capture, analysis and JSON rendering, but exclude project setup.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from benchmark_checks import create_project, git, summarize
from benchmark_client import invoke, run, shutdown
from profile_checks import self_edits


def snapshot(repo: Path, parent: Path, base: str) -> dict[str, Path]:
    paths = {name: parent / name for name in ("baseline", "candidate")}
    archive = run(["git", "archive", base], repo).stdout
    for root in paths.values():
        root.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(root, filter="data")
    tracked = run(["git", "ls-files", "-z"], repo).stdout.decode().split("\0")
    for relative in filter(None, tracked):
        original = repo / relative
        target = paths["candidate"] / relative
        if original.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        elif target.is_file():
            target.unlink()
    # A shared executable/environment is valid only for Python-only changes.
    for relative in ("Cargo.toml", "Cargo.lock", "pyproject.toml", "uv.lock"):
        if (paths["baseline"] / relative).read_bytes() != (
            paths["candidate"] / relative
        ).read_bytes():
            raise ValueError(f"{relative} differs; compare separate installed wheels")
    native_sources = {
        name: {
            path.relative_to(root): path.read_bytes()
            for path in (root / "native").rglob("*.rs")
        }
        for name, root in paths.items()
    }
    if native_sources["baseline"] != native_sources["candidate"]:
        raise ValueError("Native sources differ; compare separate installed wheels")
    return paths


def measure(
    roots: dict[str, Path],
    sources: dict[str, Path],
    args: argparse.Namespace,
    flags: list[str],
    *,
    mode: str = "native",
) -> dict[str, Any]:
    edits = args.edits
    samples: dict[str, list[float]] = {name: [] for name in roots}
    first = {}
    for name, root in roots.items():
        elapsed, _ = invoke(root, sources[name], args, flags, mode=mode)
        first[name] = elapsed
    verified = 0
    independent: dict[str, list[float]] = {name: [] for name in roots}
    for index in range(args.repeats):
        if edits:
            for name, path in edits.items():
                previous = (
                    "new_public_helper"
                    if index == 0
                    else f"edited_{mode}_helper_{index - 1}"
                )
                content = path.read_text()
                if previous not in content:
                    raise ValueError(f"Expected edit target {previous} in {path}")
                path.write_text(
                    content.replace(previous, f"edited_{mode}_helper_{index}")
                )
                git(roots[name], "add", path.relative_to(roots[name]).as_posix())
        order = list(roots)
        if index % 2:
            order.reverse()
        reports = {}
        for name in order:
            elapsed, reports[name] = invoke(
                roots[name], sources[name], args, flags, mode=mode
            )
            samples[name].append(elapsed)
        sys.stdout.write(
            f"  {mode} sample {index + 1}: { {name: round(samples[name][-1], 3) for name in order} }\n"
        )
        sys.stdout.flush()
        # Independent checking is outside the timing. Check every distinct edit,
        # and one retry per scenario. Compare complete reports within each source.
        if edits or index == 0:
            for name, report in reports.items():
                elapsed, expected = invoke(
                    roots[name],
                    sources[name],
                    args,
                    [*flags, "--no-cache"],
                    mode="foreground",
                )
                if report != expected:
                    raise RuntimeError(f"Cached/uncached reports differ: {name}")
                independent[name].append(elapsed)
                verified += 1
    return {
        "mode": mode,
        "flags": flags,
        "first_seconds": first,
        "samples_seconds": samples,
        "distributions_seconds": {
            key: summarize(value) for key, value in samples.items()
        },
        "cached_uncached_comparisons": verified,
        "independent_foreground_no_cache_seconds": independent,
    }


def benchmark(args: argparse.Namespace, parent: Path) -> dict[str, Any]:
    repo = Path(__file__).resolve().parent.parent
    result: dict[str, Any] = {
        "base": run(["git", "rev-parse", args.base], repo).stdout.decode().strip(),
        "head": run(["git", "rev-parse", "HEAD"], repo).stdout.decode().strip(),
        "dirty_status": run(["git", "status", "--porcelain"], repo).stdout.decode(),
        "diff_sha256": hashlib.sha256(
            run(["git", "diff", args.base], repo).stdout
        ).hexdigest(),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
            "processor": platform.processor(),
        },
        "native_sha256": hashlib.sha256(args.native.read_bytes()).hexdigest(),
        "repeats": args.repeats,
        "method": "The full-policy edit measures seven distinct staged API renames with the candidate analyzer and an independent final uncached check. Other cases use alternating fresh client processes; isolated source snapshots, repositories and caches; shared unchanged native binary and Python dependencies. First calls are individual observations, not statistical cold-start estimates. --no-cache correctness checks use foreground execution. No OS page-cache eviction.",
        "cases": {},
    }
    result["full_policy_edit"] = self_edits(
        repo, parent, native=args.native, revision=result["base"]
    )
    if args.full_policy_only:
        return result
    sources = snapshot(repo, parent, args.base)
    cases = result["cases"]
    for modules in args.modules:
        sys.stdout.write(f"Transitive fixture: {modules} modules\n")
        sys.stdout.flush()
        roots = {name: parent / f"transitive-{modules}-{name}" for name in sources}
        fixture = argparse.Namespace(modules=modules, methods=12, policy="transitive")
        edits = {}
        for name, root in roots.items():
            root.mkdir()
            edits[name] = create_project(fixture, root)
        for mode in ("native", "hook"):
            args.edits = None
            cases[f"transitive_{modules}_{mode}_retry"] = measure(
                roots, sources, args, ["--staged"], mode=mode
            )
            args.edits = edits
            cases[f"transitive_{modules}_{mode}_edit"] = measure(
                roots, sources, args, ["--staged"], mode=mode
            )
            # Reset helper names before the next mode without clearing caches.
            for name, path in edits.items():
                path.write_text(
                    path.read_text().replace(
                        f"edited_{mode}_helper_{args.repeats - 1}", "new_public_helper"
                    )
                )
                git(roots[name], "add", path.relative_to(roots[name]).as_posix())
            shutdown(args.sessions)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    args.edits = None
    roots = {name: parent / f"self-{name}" for name in sources}
    for root in roots.values():
        run(["git", "clone", "--quiet", "--shared", str(repo), str(root)], repo)
        git(root, "checkout", "--quiet", result["base"])
    for flags, label in (
        (["--staged"], "staged"),
        ([], "full"),
        (["--changed", "--base", result["base"]], "changed"),
    ):
        for mode in ("native", "foreground"):
            sys.stdout.write(f"Self-check: {label} {mode}\n")
            sys.stdout.flush()
            cases[f"self_{label}_{mode}"] = measure(
                roots, sources, args, flags, mode=mode
            )
    cases["self_staged_hook"] = measure(roots, sources, args, ["--staged"], mode="hook")
    cases["self_staged_python"] = measure(
        roots, sources, args, ["--staged"], mode="python"
    )
    return result


def regressions(
    result: dict[str, Any], *, percent: float, milliseconds: float
) -> list[str]:
    failures = []
    for name, case in result["cases"].items():
        distributions = case["distributions_seconds"]
        baseline = distributions["baseline"]["median"]
        candidate = distributions["candidate"]["median"]
        delta = candidate - baseline
        if delta > milliseconds / 1000 and candidate > baseline * (1 + percent / 100):
            failures.append(name)
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument(
        "--native",
        type=Path,
        default=Path(sys.executable).with_name(
            "smelt.exe" if os.name == "nt" else "smelt"
        ),
    )
    parser.add_argument(
        "--runner",
        type=Path,
        default=Path(sys.executable).with_name(
            "prek.exe" if os.name == "nt" else "prek"
        ),
    )
    parser.add_argument(
        "--modules",
        type=int,
        nargs="*",
        default=[25, 250, 1000],
        help="Synthetic fixture sizes; omit values to measure only the real repository",
    )
    parser.add_argument(
        "--full-policy-only",
        action="store_true",
        help="Run only the seven-sample API rename workload",
    )
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--regression-percent", type=float, default=20)
    parser.add_argument("--regression-ms", type=float, default=25)
    args = parser.parse_args()
    if args.full_policy_only and args.fail_on_regression:
        parser.error(
            "the percentage gate requires paired baseline and candidate measurements"
        )
    if min([*args.modules, args.repeats]) < 1:
        parser.error("modules and repeats must be positive")
    args.native, args.runner = args.native.resolve(), args.runner.resolve()
    with tempfile.TemporaryDirectory(prefix="smelt-performance-") as temporary:
        parent = Path(temporary)
        args.sessions = parent / "sessions"
        try:
            result = benchmark(args, parent)
            if not args.full_policy_only:
                result["regression_gate"] = {
                    "percent": args.regression_percent,
                    "milliseconds": args.regression_ms,
                    "failures": regressions(
                        result,
                        percent=args.regression_percent,
                        milliseconds=args.regression_ms,
                    ),
                }
            args.output.write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )
        finally:
            shutdown(args.sessions)
    sys.stdout.write(f"Wrote {args.output}\n")
    if args.fail_on_regression and result["regression_gate"]["failures"]:
        sys.stdout.write(
            f"Performance regression: {result['regression_gate']['failures']}\n"
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
