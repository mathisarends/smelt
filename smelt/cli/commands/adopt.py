from typing import TYPE_CHECKING

from smelt.analysis.parsing import AnalysisError
from smelt.cli.support import EXIT_OK, CliError, Console
from smelt.config import CONFIG_FILENAME, CONFIG_FILENAMES, load_config
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions, run_check
from smelt.engine.inference import infer_config, render_config

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from smelt.engine.inference import InferredConfig


def init(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    existing = [cwd / name for name in CONFIG_FILENAMES if (cwd / name).exists()]
    if existing and not args.force:
        msg = f"{existing[0].name} already exists (use --force to overwrite)"
        raise CliError(msg)
    inferred = infer_config(cwd)
    if inferred is None:
        msg = (
            "no Python package found in this directory or in src/; "
            "run `smelt init` from the project root"
        )
        raise CliError(msg)
    target = cwd / CONFIG_FILENAME
    for path in existing:
        if path != target:
            path.unlink()
    target.write_text(render_config(inferred), encoding="utf-8", newline="\n")
    console.print(f"Wrote {CONFIG_FILENAME}")
    for line in _summary(inferred):
        console.print(f"  {line}")
    console.print(_violation_summary(target))
    return EXIT_OK


def _summary(inferred: InferredConfig) -> list[str]:
    lines = [f"root packages: {', '.join(inferred.root_packages)}"]
    if inferred.features:
        lines.append(f"features: {', '.join(inferred.features)}")
    if inferred.layers:
        lines.append(
            "layers: "
            + ", ".join(f"{layer.name} ({layer.path})" for layer in inferred.layers)
        )
    else:
        lines.append("layers: none detected (see the commented example)")
    if inferred.shared:
        lines.append(f"shared: {', '.join(inferred.shared)}")
    if inferred.composition_root:
        lines.append(f"composition root: {', '.join(inferred.composition_root)}")
    if inferred.di_frameworks:
        lines.append(f"DI frameworks: {', '.join(inferred.di_frameworks)}")
    return lines


def _violation_summary(path: Path) -> str:
    try:
        outcome = run_check(load_config(path), CheckOptions(use_baseline=False))
    except AnalysisError as exc:
        return f"Could not check the inferred config: {exc}"
    report = outcome.report
    errors = report.count(Severity.ERROR)
    warnings = report.count(Severity.WARNING)
    if not errors and not warnings:
        return "The inferred config yields no violations. Run `smelt check` any time."
    return (
        f"The inferred config yields {errors} error{'s' * (errors != 1)} and "
        f"{warnings} warning{'s' * (warnings != 1)}. Review {CONFIG_FILENAME}, then run "
        "`smelt check`, or `smelt baseline` to adopt incrementally."
    )
