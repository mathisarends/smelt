"""Profile warmed CLI requests and one-file edits without touching the caller's index."""

from __future__ import annotations

import argparse
import contextlib
import cProfile
import importlib
import io
import json
import os
import pstats
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from benchmark_checks import create_project, git, summarize
from benchmark_client import invoke, run, shutdown

from smelt.analysis.identity import analyzer_identity
from smelt.analysis.retained import RetainedAnalysis, retain
from smelt.cli.app import main as smelt_main

_LARGE_MODULES = 1000


def request(root: Path, flags: list[str], state: RetainedAnalysis) -> float:
    started = time.perf_counter()
    analyzer_identity.cache_clear()
    analyzer_identity()
    with (
        contextlib.chdir(root),
        retain(state),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        code = smelt_main(["check", *flags, "--format", "json", "--fail-on", "warning"])
    if code not in {0, 1}:
        raise RuntimeError(f"Check returned {code}")
    return time.perf_counter() - started


def profile(root: Path, flags: list[str], state: RetainedAnalysis) -> dict[str, Any]:
    engine = importlib.import_module("smelt.engine.check")
    original = engine.rule_findings
    rules: dict[str, float] = {}

    def timed(rule: Any, ctx: Any) -> Any:
        started = time.perf_counter()
        try:
            yield from original(rule, ctx)
        finally:
            rules[rule.code] = rules.get(rule.code, 0) + time.perf_counter() - started

    engine.rule_findings = timed
    profiler = cProfile.Profile()
    try:
        elapsed = profiler.runcall(request, root, flags, state)
    finally:
        engine.rule_findings = original
    stats = pstats.Stats(profiler)
    rows = []
    for (file, line, function), (
        primitive,
        calls,
        own,
        cumulative,
        _,
    ) in stats.stats.items():
        path = file.replace("\\", "/")
        for marker in ("/smelt/", "/Lib/", "/scripts/"):
            if marker in path:
                path = path[path.rfind(marker) + 1 :]
                break
        rows.append(
            {
                "file": path,
                "line": line,
                "function": function,
                "calls": calls,
                "primitive_calls": primitive,
                "own_seconds": own,
                "cumulative_seconds": cumulative,
            }
        )
    return {
        "profiled_seconds": elapsed,
        "rule_seconds_including_shared_lazy_indexes": dict(
            sorted(rules.items(), key=lambda pair: pair[1], reverse=True)
        ),
        "top_cumulative": sorted(
            rows, key=lambda row: row["cumulative_seconds"], reverse=True
        )[:60],
        "top_own": sorted(rows, key=lambda row: row["own_seconds"], reverse=True)[:30],
    }


def cold(root: Path, *, daemon: bool, sessions: Path) -> dict[str, Any]:
    environment = {
        **os.environ,
        "SMELT_DAEMON": "1" if daemon else "0",
        "SMELT_TRACE": "1",
        "LOCALAPPDATA": str(sessions),
        "XDG_CACHE_HOME": str(sessions),
    }
    started = time.perf_counter()
    result = subprocess.run(  # noqa: S603 - installed native binary, fixed arguments
        [os.environ["SMELT_NATIVE_CLIENT"], "check", "--staged", "--format", "json"],
        cwd=root,
        env=environment,
        capture_output=True,
        check=False,
        timeout=180,
    )
    elapsed = time.perf_counter() - started
    if result.returncode not in {0, 1}:
        raise RuntimeError(result.stderr.decode())
    report = json.loads(result.stdout)
    return {
        "seconds": elapsed,
        "daemon": daemon,
        "trace": result.stderr.decode(),
        "status": report["status"],
    }


def self_edits(
    repo: Path, parent: Path, *, native: Path, revision: str
) -> dict[str, Any]:
    root = parent / "self-edits"
    run(["git", "clone", "--quiet", "--shared", str(repo), str(root)], repo)
    git(root, "checkout", "--quiet", revision)
    source = Path(importlib.import_module("smelt").__file__).resolve().parent.parent
    options = argparse.Namespace(native=native, sessions=parent / "sessions")
    path = root / "smelt/config/errors.py"
    with path.open("a", encoding="utf-8") as output:
        output.write("\ndef new_public_helper():\n    return 1\n")
    git(root, "add", path.relative_to(root).as_posix())
    try:
        first, _ = invoke(root, source, options, ["--staged"])
        samples = []
        for index in range(7):
            previous = "new_public_helper" if index == 0 else f"edit_helper_{index - 1}"
            path.write_text(path.read_text().replace(previous, f"edit_helper_{index}"))
            git(root, "add", path.relative_to(root).as_posix())
            elapsed, report = invoke(root, source, options, ["--staged"])
            samples.append(elapsed)
            sys.stdout.write(f"Self edit {index + 1}: {elapsed:.3f}s\n")
            sys.stdout.flush()
        uncached, expected = invoke(
            root, source, options, ["--staged", "--no-cache"], mode="foreground"
        )
        if report != expected:
            raise RuntimeError("Real-project edit differs from uncached checking")
        incremental = profile_edit(root, path, f"edit_helper_{len(samples) - 1}")
        return {
            "first_seconds": first,
            "samples_seconds": samples,
            "distribution_seconds": {**summarize(samples), "min": min(samples)},
            "independent_no_cache_seconds": uncached,
            "final_report_matches_uncached": True,
            "incremental_profile": incremental,
        }
    finally:
        shutdown(options.sessions)


def profile_edit(root: Path, path: Path, previous: str) -> dict[str, Any]:
    state = RetainedAnalysis()
    for name in ("primed_profile_helper", "changed_profile_helper"):
        content = path.read_text()
        if previous not in content:
            raise ValueError(f"Expected {previous} in {path}")
        path.write_text(content.replace(previous, name))
        git(root, "add", path.relative_to(root).as_posix())
        previous = name
        if name == "primed_profile_helper":
            request(root, ["--staged"], state)
    return profile(root, ["--staged"], state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--project", type=Path, default=Path(__file__).resolve().parent.parent
    )
    args = parser.parse_args()
    repo = args.project.resolve()
    os.environ["SMELT_NATIVE_CLIENT"] = str(
        Path(sys.executable).with_name("smelt.exe" if os.name == "nt" else "smelt")
    )
    result: dict[str, Any] = {
        "method": "In-process CLI after imports; analyzer identity refreshed and RetainedAnalysis scoped like the worker. Native Git enabled. cProfile adds overhead; cumulative rows overlap. First rule accessing a shared index pays its construction. Fixtures and caches are separate from latency benchmarks. Synthetic edits rename a newly staged function. Self checks use the current checkout read-only.",
        "cases": {},
    }
    for flags, name in (
        ([], "self_full_warm"),
        (["--no-cache"], "self_full_uncached"),
        (["--staged"], "self_staged_retry"),
        (["--changed", "--base", "origin/main"], "self_changed_warm"),
    ):
        state = RetainedAnalysis()
        request(repo, flags, state)
        unprofiled = request(repo, flags, state)
        result["cases"][name] = profile(repo, flags, state)
        result["cases"][name]["unprofiled_in_process_seconds"] = unprofiled
        sys.stdout.write(
            f"{name}: {result['cases'][name]['profiled_seconds']:.3f}s profiled\n"
        )
        sys.stdout.flush()
    with tempfile.TemporaryDirectory(prefix="smelt-profiling-") as temporary:
        result["self_edit_latency"] = self_edits(
            repo,
            Path(temporary),
            native=Path(os.environ["SMELT_NATIVE_CLIENT"]),
            revision="HEAD",
        )
        for modules in (250, _LARGE_MODULES):
            root = Path(temporary) / str(modules)
            root.mkdir()
            fixture = argparse.Namespace(
                modules=modules, methods=12, policy="transitive"
            )
            changed = create_project(fixture, root)
            if modules == _LARGE_MODULES:
                sessions = Path(temporary) / "sessions"
                try:
                    result["cold_1000_daemon"] = cold(
                        root, daemon=True, sessions=sessions
                    )
                    foreground = Path(temporary) / "foreground"
                    foreground.mkdir()
                    create_project(fixture, foreground)
                    result["cold_1000_foreground"] = cold(
                        foreground, daemon=False, sessions=sessions
                    )
                finally:
                    shutdown(sessions)
            state = RetainedAnalysis()
            request(root, ["--staged"], state)
            changed.write_text(
                changed.read_text().replace(
                    "new_public_helper", "profiled_public_helper"
                )
            )
            git(root, "add", changed.relative_to(root).as_posix())
            name = f"transitive_{modules}_staged_edit"
            result["cases"][name] = profile(root, ["--staged"], state)
            sys.stdout.write(
                f"{name}: {result['cases'][name]['profiled_seconds']:.3f}s profiled\n"
            )
            sys.stdout.flush()
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
