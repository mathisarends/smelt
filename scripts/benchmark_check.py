"""Time `smelt check` end to end on the repository and on a synthetic project.

Each workload runs a fixed number of times per installation, alternating the
order. The synthetic project has layered packages, cross-module imports and
mirrored tests, so discovery, the import graph, coverage and test rules all scale
with `--modules`. `--smelt NAME=PATH` times existing installations instead of
building disposable ones, which is quicker while iterating locally.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from benchmark_installs import git, install, isolated_env

# A candidate regresses when its median exceeds the base by both margins.
_MIN_REGRESSION_SECONDS = 0.1
_MAX_REGRESSION_RATIO = 1.25
_LAYERS = ("config", "model", "analysis", "rules", "engine", "cli")
_CONFIG = """\
version: 2

project:
  root_packages: [corpus]
  source_roots: ["."]
  test_roots: [tests]

architecture:
  layers:
{layers}
  import_graph:
    transitive: true

style:
  forbidden_names: [utils, helpers]
  private_modules: forbid
  dataclass_options: [slots]

tests:
  layout: mirror

rules:
  architecture/layer-boundary: error
  architecture/import-cycle: error
  architecture/uncovered-source: error
  imports/prefer-package-import: warning
  layout/definition-order: error
  layout/forbidden-module-name: error
  layout/forbidden-private-module: error
  layout/callee-before-caller: warning
  modeling/missing-dataclass-option: warning
  modeling/str-enum-value-convention: warning
  modeling/duplicate-class-name: warning
  complexity/excess-positional-parameters: {{severity: warning, max: 3}}
  complexity/excess-attribute-depth: warning
  complexity/duplicate-function: warning
  visibility/internal-public-member: warning
  visibility/internal-public-module-symbol: warning
  visibility/test-only-public-function: warning
  visibility/foreign-private-access: warning
  idioms/redundant-name-alias: warning
  typing/duplicate-literal-type: hint
  tests/misplaced-test: error
  tests/private-access: warning
  tests/vacuous-assertion: warning
  tests/missing-outcome-check: warning
"""
_MODULE = '''\
"""Module {index} of the {layer} layer."""

from dataclasses import dataclass
from enum import StrEnum
{imports}


class Kind{index}(StrEnum):
    FIRST = "first"
    SECOND = "second"


@dataclass(frozen=True, slots=True)
class Record{index}:
    name: str
    size: int
    kind: Kind{index} = Kind{index}.FIRST

    def doubled(self) -> int:
        return self.size * 2


def build_{index}(name: str, size: int) -> Record{index}:
    total = size
{calls}
    return Record{index}(name, total)
'''
_TEST = """\
from corpus.{layer}.module_{index} import Kind{index}, build_{index}


def test_build_{index}_keeps_the_name() -> None:
    record = build_{index}("name", 1)

    assert record.name == "name"
    assert record.kind is Kind{index}.FIRST
"""


def write_corpus(root: Path, modules: int) -> None:
    """A deterministic layered project with `modules` modules and their tests."""
    chooser = random.Random(modules)  # noqa: S311 - a reproducible layout, not a secret
    layers = "\n".join(
        f"    {layer}:\n      path: {layer}\n      may_depend_on: [{', '.join(_LAYERS[:i])}]"
        for i, layer in enumerate(_LAYERS)
    )
    (root / "smelt.yml").write_text(_CONFIG.format(layers=layers), encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "corpus"\nversion = "0"\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    placed: list[tuple[int, str]] = []
    for package in ("corpus", "tests"):
        (root / package).mkdir()
        (root / package / "__init__.py").write_text("", encoding="utf-8")
        for layer in _LAYERS:
            (root / package / layer).mkdir()
            (root / package / layer / "__init__.py").write_text("", encoding="utf-8")
    for number in range(modules):
        index = f"{number:05d}"
        layer_index = number * len(_LAYERS) // modules
        layer = _LAYERS[layer_index]
        lower = [entry for entry in placed if _LAYERS.index(entry[1]) < layer_index]
        dependencies = sorted(chooser.sample(lower, min(3, len(lower))))
        imports = "".join(
            f"\nfrom corpus.{dep_layer}.module_{dep:05d} import build_{dep:05d}"
            for dep, dep_layer in dependencies
        )
        calls = "\n".join(
            f'    total += build_{dep:05d}("{index}", 1).size'
            for dep, _ in dependencies
        )
        (root / "corpus" / layer / f"module_{index}.py").write_text(
            _MODULE.format(
                index=index,
                layer=layer,
                imports=imports,
                calls=calls or "    total += 0",
            ),
            encoding="utf-8",
        )
        (root / "tests" / layer / f"test_module_{index}.py").write_text(
            _TEST.format(index=index, layer=layer), encoding="utf-8"
        )
        placed.append((number, layer))
    git(root, "init", "--quiet")
    git(root, "add", "--all")
    git(root, "commit", "--quiet", "-m", "corpus")


def executable(root: Path) -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return root / ".venv" / scripts / f"smelt{suffix}"


def run_check(
    smelt: Path, cwd: Path, args: list[str], *, env: dict[str, str]
) -> tuple[float, str]:
    """The seconds one check took, and its stderr."""
    start = time.perf_counter()
    completed = subprocess.run(  # noqa: S603 - the installation under measurement
        [str(smelt), "check", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        check=False,
    )
    elapsed = time.perf_counter() - start
    # Findings exit 1; anything else is a broken workload, not a timing.
    if completed.returncode not in (0, 1):
        output = (completed.stdout + completed.stderr).decode(errors="replace")
        raise RuntimeError(f"{smelt} check {args} failed in {cwd}:\n{output}")
    return elapsed, completed.stderr.decode(errors="replace")


def _edit(corpus: Path, sample: int) -> Path:
    """Change one module's behavior, so each sample checks a new state."""
    path = corpus / "corpus" / "engine" / "module_edit.py"
    path.write_text(
        f'"""An edited module."""\n\n\ndef edited() -> int:\n    return {sample}\n',
        encoding="utf-8",
    )
    return path


def workloads(
    self_roots: dict[str, Path], corpus: Path
) -> dict[str, tuple[dict[str, Path], list[str], bool]]:
    """Each workload's directories by installation, its arguments and whether it edits."""
    corpora = dict.fromkeys(self_roots, corpus)
    return {
        "self: strict full check": (
            self_roots,
            ["--strict-coverage", "--fail-on", "warning"],
            False,
        ),
        "synthetic: full check": (corpora, [], False),
        "synthetic: staged edit": (corpora, ["--staged"], True),
        "synthetic: changed edit": (corpora, ["--changed", "--base", "HEAD"], True),
    }


def benchmark(
    smelts: dict[str, Path],
    self_roots: dict[str, Path],
    corpus: Path,
    *,
    repeats: int,
) -> tuple[dict[str, dict[str, list[float]]], dict[str, dict[str, str]]]:
    env = {**os.environ}
    env.pop("SMELT_PROFILE", None)
    profiled = {**env, "SMELT_PROFILE": "1"}
    times: dict[str, dict[str, list[float]]] = {}
    profiles: dict[str, dict[str, str]] = {}
    for workload, (roots, args, edits) in workloads(self_roots, corpus).items():
        samples: dict[str, list[float]] = {name: [] for name in smelts}
        times[workload] = samples
        # The first sample warms file system caches and is not counted.
        for i in range(repeats + 1):
            if edits:
                edited = _edit(corpus, i)
                if "--staged" in args:
                    git(corpus, "add", "--", str(edited.relative_to(corpus)))
            order = list(reversed(smelts)) if i % 2 else list(smelts)
            for name in order:
                elapsed, _ = run_check(smelts[name], roots[name], args, env=env)
                if i:
                    samples[name].append(elapsed)
                sys.stdout.write(f"{workload}, {name}, sample {i}: {elapsed:.3f}s\n")
                sys.stdout.flush()
        profiles[workload] = {
            name: run_check(smelts[name], roots[name], args, env=profiled)[1]
            for name in smelts
        }
        if edits:
            git(corpus, "reset", "--quiet", "--hard", "HEAD")
            git(corpus, "clean", "--quiet", "--force")
    return times, profiles


def summarize(
    times: dict[str, dict[str, list[float]]], names: list[str]
) -> tuple[str, list[str]]:
    """A Markdown table of the medians, and the workloads the last name regressed."""
    base, candidate = names[0], names[-1]
    lines = [
        "## Check latency",
        "",
        "Median seconds per workload.",
        "",
        "| Workload | " + " | ".join(names) + " | Change |",
        "| --- |" + " ---: |" * len(names) + " ---: |",
    ]
    failures = []
    for workload, samples in times.items():
        medians = {name: statistics.median(samples[name]) for name in names}
        change = medians[candidate] - medians[base]
        cells = " | ".join(f"{medians[name]:.3f}" for name in names)
        lines.append(
            f"| {workload} | {cells} | {change:+.3f} s ({change / medians[base]:+.1%}) |"
        )
        if (
            change > _MIN_REGRESSION_SECONDS
            and medians[candidate] > medians[base] * _MAX_REGRESSION_RATIO
        ):
            failures.append(workload)
    return "\n".join(lines) + "\n", failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--candidate", default="HEAD")
    parser.add_argument(
        "--smelt",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="time this installed smelt executable instead of building revisions",
    )
    parser.add_argument("--modules", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1 or args.modules < len(_LAYERS):
        parser.error("repeats must be positive and modules at least one per layer")
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="smelt-check-latency-") as temporary:
        parent = Path(temporary)
        if args.smelt:
            smelts = {
                name: Path(path)
                for name, path in (entry.split("=", 1) for entry in args.smelt)
            }
            self_roots = dict.fromkeys(smelts, repo)
        else:
            self_roots = install(
                repo,
                {"base": args.base, "candidate": args.candidate},
                parent=parent,
                env=isolated_env(repo),
            )
            smelts = {name: executable(root) for name, root in self_roots.items()}
        corpus = parent / "corpus"
        corpus.mkdir()
        write_corpus(corpus, args.modules)
        times, profiles = benchmark(smelts, self_roots, corpus, repeats=args.repeats)
    args.output.write_text(
        json.dumps({"times": times, "profiles": profiles}, indent=2) + "\n",
        encoding="utf-8",
    )
    table, failures = summarize(times, list(smelts))
    sys.stdout.write(table)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary_path).open("a", encoding="utf-8") as output:
            output.write(table)
    if failures and not args.smelt:
        sys.exit("Check latency regressed: " + ", ".join(failures))


if __name__ == "__main__":
    main()
