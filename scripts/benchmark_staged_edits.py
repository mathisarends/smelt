"""Time the repository's complete pre-commit chain on fresh staged Python edits."""

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

from benchmark_checks import git
from benchmark_client import shutdown

# Change executable Python in existing, independently passing files. A missing
# anchor is a broken workload and must never turn into an exact-result retry.
_MIN_REGRESSION_SECONDS = 0.35
_EDITS = {
    "test body": (
        "tests/engine/test_file_checks.py",
        "def stable(): pass",
        "def stable(): return {}",
    ),
    "rule implementation": (
        "smelt/rules/structure/narrowing_asserts.py",
        "_MIN_METHODS = 2",
        "_MIN_METHODS = {}",
    ),
    "core implementation": ("smelt/config/errors.py", "cutoff=0.6)", "cutoff=0.6{})"),
}


def command(root: Path, args: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed benchmark commands
        args,
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = completed.stdout.decode(errors="replace")
    if completed.returncode:
        raise RuntimeError(f"{args} failed in {root}:\n{output}")
    return output.strip()


def benchmark(
    args: argparse.Namespace, parent: Path
) -> dict[str, dict[str, list[float]]]:
    repo = Path(__file__).resolve().parents[1]
    roots = {name: parent / name for name in ("base", "candidate")}
    sessions = parent / "sessions"
    env = {
        **os.environ,
        "UV_CACHE_DIR": command(repo, ["uv", "cache", "dir"]),
        "UV_PYTHON_INSTALL_DIR": command(repo, ["uv", "python", "dir"]),
        "PREK_HOME": command(repo, ["uv", "run", "--frozen", "prek", "cache", "dir"]),
        "SMELT_DAEMON": "1",
        "LOCALAPPDATA": str(sessions),
        "XDG_CACHE_HOME": str(sessions),
    }
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    env.pop("VIRTUAL_ENV", None)
    hooks = ["uv", "run", "--frozen", "prek", "run"]
    try:
        for name, revision in (("base", args.base), ("candidate", args.candidate)):
            sha = command(
                repo, ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"]
            )
            git(
                repo,
                "clone",
                "--quiet",
                "--shared",
                "--no-checkout",
                str(repo),
                str(roots[name]),
            )
            git(roots[name], "checkout", "--quiet", "--detach", sha)
            command(roots[name], ["uv", "sync", "--frozen"], env=env)
            sys.stdout.write(f"{name}: {sha}\n")
            sys.stdout.flush()
        full_check: dict[str, list[float]] = {"base": [], "candidate": []}
        times = {"strict full check after test edit": full_check}
        for scenario, (path, anchor, replacement) in _EDITS.items():
            samples: dict[str, list[float]] = {"base": [], "candidate": []}
            times[scenario] = samples
            originals = {
                name: (root / path).read_bytes() for name, root in roots.items()
            }
            for i in range(args.repeats + 1):
                for name in reversed(roots) if i % 2 else roots:
                    root = roots[name]
                    original = originals[name]
                    if original.count(anchor.encode()) != 1:
                        raise ValueError(f"Expected one {anchor!r} in {name}/{path}")
                    (root / path).write_bytes(
                        original.replace(
                            anchor.encode(), replacement.format(i + 3).encode()
                        )
                    )
                    git(root, "add", "--", path)
                    start = time.perf_counter()
                    command(root, hooks, env=env)
                    elapsed = time.perf_counter() - start
                    if i:
                        samples[name].append(elapsed)
                    sys.stdout.write(
                        f"{scenario}, {name}, sample {i}: {elapsed:.2f}s\n"
                    )
                    sys.stdout.flush()
                    if scenario == "test body":
                        start = time.perf_counter()
                        command(
                            root,
                            [
                                "uv",
                                "run",
                                "--frozen",
                                "smelt",
                                "check",
                                "--strict-coverage",
                                "--fail-on",
                                "warning",
                            ],
                            env=env,
                        )
                        if i:
                            full_check[name].append(time.perf_counter() - start)
            for root in roots.values():
                git(
                    root,
                    "restore",
                    "--source=HEAD",
                    "--staged",
                    "--worktree",
                    "--",
                    path,
                )
        return times
    finally:
        shutdown(sessions)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--candidate", default="HEAD")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    with tempfile.TemporaryDirectory(prefix="smelt-hook-edits-") as temporary:
        samples = benchmark(args, Path(temporary))
    args.output.write_text(json.dumps(samples, indent=2) + "\n", encoding="utf-8")
    summary = [
        "## Staged edit latency",
        "",
        f"Median of {args.repeats} fresh edits per revision, in seconds.",
        "",
        "| Workload | Base | Candidate | Time saved |",
        "| --- | ---: | ---: | ---: |",
    ]
    failures = []
    for workload, pair in samples.items():
        base = statistics.median(pair["base"])
        candidate = statistics.median(pair["candidate"])
        saved = base - candidate
        summary.append(
            f"| {workload} | {base:.2f} | {candidate:.2f} | {saved:+.2f} s ({saved / base:+.1%}) |"
        )
        if candidate - base > _MIN_REGRESSION_SECONDS and candidate > base * 1.25:
            failures.append(workload)
    table = "\n".join(summary) + "\n"
    sys.stdout.write(table)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary_path).open("a", encoding="utf-8") as output:
            output.write(table)
    if failures:
        sys.exit("Hook latency regressed: " + ", ".join(failures))


if __name__ == "__main__":
    main()
