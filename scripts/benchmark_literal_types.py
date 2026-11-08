"""Measure SMT338's in-process check overhead with and without the rule.

Includes configuration, discovery, content hashing, the production fact cache and
rule decisions. Excludes CLI startup/IPC, Git capture and report serialization.
Every edited cached report is compared with independent uncached checking.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

from smelt.config import load_config
from smelt.engine.check import CheckOptions, run_check


def source(index: int, *, dense: bool) -> str:
    active = dense or index % 10 == 0
    family = index // 10
    annotation = f'Literal["left-{family}", "right-{family}"]' if active else "str"
    header = "from typing import Literal\n" if active else ""
    return header + "\n".join(
        f"def operation_{method}(button: {annotation}, count: int) -> str:\n"
        "    total = 0\n"
        "    for step in range(count):\n"
        "        total += step\n"
        "    if total > 100:\n"
        "        return button.upper()\n"
        "    return button\n"
        for method in range(12)
    )


def invoke(root: Path, options: CheckOptions) -> tuple[float, list[dict[str, Any]]]:
    start = perf_counter()
    report = run_check(load_config(root / "smelt.yml"), options).report
    elapsed = (perf_counter() - start) * 1000
    # Serialization/comparison is deliberately outside the measured interval.
    return elapsed, [violation.to_json() for violation in report.violations]


def summarize(samples: list[float]) -> dict[str, Any]:
    return {"median_ms": statistics.median(samples), "samples_ms": samples}


def benchmark(parent: Path, files: int, repeats: int, *, dense: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="literal-types-", dir=parent) as directory:
        root = Path(directory)
        package = root / "app"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (root / "smelt.yml").write_text(
            "version: 1\nproject: {root_packages: [app]}\n"
            "rules: {duplicate-literal-type: hint}\n",
            encoding="utf-8",
        )
        for index in range(files):
            (package / f"module_{index:04d}.py").write_text(
                source(index, dense=dense), encoding="utf-8"
            )
        enabled = CheckOptions(select=("SMT338",))
        disabled = replace(enabled, ignore=("SMT338",))
        fresh = replace(enabled, use_cache=False)
        uncached_ms, expected = invoke(root, fresh)
        first_cached_ms, actual = invoke(root, enabled)
        if actual != expected:
            raise RuntimeError("First cached report differs from uncached checking")
        warm: dict[str, list[float]] = {"disabled": [], "enabled": []}
        edits: dict[str, list[float]] = {"disabled": [], "enabled": []}
        edit_path = package / "module_0000.py"
        original = edit_path.read_text(encoding="utf-8")
        for repetition in range(repeats):
            order = [("disabled", disabled), ("enabled", enabled)]
            if repetition % 2:
                order.reverse()
            for name, options in order:
                elapsed, result = invoke(root, options)
                warm[name].append(elapsed)
                if result != (expected if name == "enabled" else []):
                    raise RuntimeError("Warm report differs from uncached checking")
            edit_path.write_text(
                original + f"\nREVISION = {repetition}\n", encoding="utf-8"
            )
            for name, options in order:
                elapsed, result = invoke(root, options)
                edits[name].append(elapsed)
                if name == "enabled":
                    _, independent = invoke(root, fresh)
                    if result != independent:
                        raise RuntimeError(
                            "Edited cached report differs from uncached checking"
                        )
        return {
            "files": files,
            "literal_files": files if dense else len(range(0, files, 10)),
            "findings": len(expected),
            "uncached_enabled_ms": uncached_ms,
            "first_cached_enabled_ms": first_cached_ms,
            "warm": {name: summarize(samples) for name, samples in warm.items()},
            "edit": {name: summarize(samples) for name, samples in edits.items()},
            "reports_match": True,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, nargs="+", default=[250, 1000])
    parser.add_argument("--repeats", type=int, default=9)
    parser.add_argument("--dense", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1 or any(count < 1 for count in args.files):
        parser.error("files and repeats must be positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "python": sys.version,
        "platform": platform.platform(),
        "dense": args.dense,
        "measurement": __doc__,
        "results": [],
    }
    for files in args.files:
        result["results"].append(
            benchmark(args.output.parent, files, args.repeats, dense=args.dense)
        )
        args.output.write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        sys.stdout.write(f"completed {files} files\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
