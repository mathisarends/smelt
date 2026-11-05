"""Measure optional filename policies through the native CLI and complete prek hook."""

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

from smelt.analysis.files import FileIndex
from smelt.config import load_config
from smelt.engine import native_git
from smelt.engine.changes import capture_index
from smelt.engine.git_source import GitSourceStore


class _DirectoryStore(GitSourceStore):
    """Reproduce the initial virtual-directory traversal on the same snapshot."""

    def yaml_inventory(self, root: Path) -> None:
        return None


def inventory_benchmark(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    os.environ["SMELT_NATIVE_CLIENT"] = str(args.analyzer)
    with native_git.check_scope(enabled=True):
        if native_git.capture(root) is None:
            raise RuntimeError("native index capture unavailable")
        index = capture_index(root)
    if index.yaml_paths is None:
        raise RuntimeError("native binary does not supply YAML candidates")
    config = load_config(root / "smelt.yml").config
    stores = {
        "rust_candidates": GitSourceStore.from_index(index),
        "python_flat_fallback": GitSourceStore(index.repository, index.blobs),
        "initial_directory_walk": _DirectoryStore(index.repository, index.blobs),
    }
    samples: dict[str, list[float]] = {name: [] for name in stores}
    expected: tuple[str, ...] | None = None
    for repeat in range(31):
        names = list(stores)
        if repeat % 2:
            names.reverse()
        for name in names:
            files = FileIndex(root, config, store=stores[name])
            started = time.perf_counter()
            paths = files.yaml_paths
            samples[name].append(time.perf_counter() - started)
            if expected is not None and paths != expected:
                raise RuntimeError("inventory implementations disagree")
            expected = paths
    return {
        "method": "31 alternating in-process inventory evaluations on the same captured index. Excludes capture, store construction and rule evaluation; complete CLI/hook samples above measure end-to-end behavior.",
        "yaml_paths": len(expected or ()),
        "samples_seconds": samples,
        "distributions_seconds": {
            name: summarize(values) for name, values in samples.items()
        },
        "inventories_match": True,
    }


def prepare(root: Path, args: argparse.Namespace, *, enabled: bool) -> Path:
    root.mkdir()
    changed = create_project(args, root)
    for index in range(args.yaml_files):
        directory = root / "deploy" / f"group_{index // 100}"
        directory.mkdir(exist_ok=True, parents=True)
        (directory / f"config_{index}.yml").write_text("key: value\n")
        (directory / f"asset_{index}.txt").write_text("unrelated\n")
    (root / "deploy/compose.yml").write_text("services: {}\n")
    config = root / "smelt.yml"
    if enabled:
        config.write_text(
            config.read_text().replace(
                "structure:\n",
                "structure:\n  file_naming: {yaml_extension: yml, compose_basename: compose}\n",
            )
        )
    hook = {
        "id": "smelt",
        "name": "smelt",
        "entry": shlex.join(
            [args.analyzer.as_posix(), "check", "--staged", "--format", "json"]
        ),
        "language": "system",
        "pass_filenames": False,
        "always_run": True,
        "verbose": True,
    }
    (root / ".pre-commit-config.yml").write_text(
        yaml.safe_dump({"repos": [{"repo": "local", "hooks": [hook]}]})
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "benchmark inventory")
    return changed


def measure(
    root: Path, args: argparse.Namespace, mode: str
) -> tuple[float, dict[str, Any]]:
    if mode == "cli":
        return invoke(root, args.analyzer, "--staged")
    environment = {**os.environ, "SMELT_DAEMON": "1", "PYTHONIOENCODING": "utf-8"}
    started = time.perf_counter()
    result = subprocess.run(  # noqa: S603 - explicit benchmark runner
        [str(args.runner), "run", "smelt", "--all-files", "--verbose"],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    elapsed = time.perf_counter() - started
    if result.returncode not in {0, 1} or "{" not in result.stdout:
        raise RuntimeError(f"hook failed: {result.stdout}\n{result.stderr}")
    report, _ = json.JSONDecoder().raw_decode(result.stdout[result.stdout.index("{") :])
    return elapsed, report


def benchmark(args: argparse.Namespace, parent: Path) -> dict[str, Any]:
    roots = {
        (mode, policy): parent / f"{mode}-{policy}"
        for mode in ("cli", "hook")
        for policy in ("disabled", "enabled")
    }
    changed = {
        key: prepare(root, args, enabled=key[1] == "enabled")
        for key, root in roots.items()
    }
    first: dict[str, float] = {}
    for (mode, policy), root in roots.items():
        elapsed, report = measure(root, args, mode)
        if report != invoke(root, args.analyzer, "--staged", "--no-cache")[1]:
            raise RuntimeError("first report differs from uncached analysis")
        first[f"{mode}_{policy}"] = elapsed
    samples: dict[str, list[float]] = {}
    for scenario in ("retry", "yaml_content", "yaml_rename", "python_edit"):
        for index in range(args.repeats):
            keys = list(roots)
            if index % 2:
                keys.reverse()
            for key in keys:
                root = roots[key]
                if scenario == "yaml_content":
                    (root / "deploy/group_0/config_0.yml").write_text(
                        f"key: value_{index}\n"
                    )
                    git(root, "add", "deploy/group_0/config_0.yml")
                elif scenario == "yaml_rename":
                    previous = (
                        "compose.yml"
                        if index == 0
                        else f"compose.variant_{index - 1}.yml"
                    )
                    (root / "deploy" / previous).rename(
                        root / "deploy" / f"compose.variant_{index}.yml"
                    )
                    git(root, "add", "deploy")
                elif scenario == "python_edit":
                    with changed[key].open("a") as stream:
                        stream.write(f"\nVALUE_{index} = {index}\n")
                    git(root, "add", changed[key].relative_to(root).as_posix())
                elapsed, report = measure(root, args, key[0])
                samples.setdefault(f"{key[0]}_{key[1]}_{scenario}", []).append(elapsed)
                expected = invoke(root, args.analyzer, "--staged", "--no-cache")[1]
                if report != expected or report["violations"]:
                    raise RuntimeError("report differs from independent clean analysis")
        sys.stderr.write(
            f"{scenario}: enabled CLI median {summarize(samples[f'cli_enabled_{scenario}'])['median']:.3f}s; "
            f"enabled hook median {summarize(samples[f'hook_enabled_{scenario}'])['median']:.3f}s\n"
        )
    return {
        "environment": {"platform": platform.platform(), "python": sys.version},
        "modules": args.modules,
        "methods_per_module": args.methods,
        "yaml_files": args.yaml_files,
        "unrelated_files": args.yaml_files,
        "repeats": args.repeats,
        "policy": args.policy,
        "runner": subprocess.run(  # noqa: S603 - explicit runner
            [str(args.runner), "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip(),
        "first_seconds": first,
        "samples_seconds": samples,
        "distributions_seconds": {
            key: summarize(values) for key, values in samples.items()
        },
        "reports_match_uncached": True,
        "inventory": inventory_benchmark(roots[("cli", "enabled")], args),
        "method": {
            "comparison": "Same candidate with policies omitted versus both policies enabled; not a comparison against a prior release.",
            "hook": "Complete prek run smelt --all-files --verbose; local system hook, native CLI, independent repository/cache per mode and policy.",
            "order": "Alternating mode/policy order each repetition.",
            "cold": "Single first observations include cache creation and worker startup; excluded from warm samples.",
            "limits": "Synthetic Windows repositories; no installation, uv wrapper or other hooks. p95 is nearest rank (maximum at seven repeats).",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analyzer", required=True, type=Path)
    parser.add_argument("--runner", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--yaml-files", type=int, default=1000)
    parser.add_argument("--modules", type=int, default=250)
    parser.add_argument("--methods", type=int, default=12)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument(
        "--policy", choices=["members", "transitive"], default="transitive"
    )
    args = parser.parse_args()
    args.analyzer, args.runner = args.analyzer.resolve(), args.runner.resolve()
    if min(args.yaml_files, args.modules, args.methods, args.repeats) < 1:
        parser.error("inventory sizes and repeats must be positive")
    with tempfile.TemporaryDirectory(prefix="smelt-filename-benchmark-") as temporary:
        result = benchmark(args, Path(temporary))
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
