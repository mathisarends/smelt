from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.analysis.parsing import AnalysisError
from smelt.cli.support import EXIT_OK, CliError, Console, load_project_config
from smelt.config import CONFIG_FILENAME, CONFIG_FILENAMES, load_config
from smelt.diagnostics.debt import Debt
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions, run_check, snippet_reader
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
            "no Python package found in this directory, src/, or declared uv workspace members; "
            "run `smelt init` from the project root or configure source_roots manually"
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
    if inferred.wiring:
        lines.append(f"wiring: {', '.join(inferred.wiring)}")
    if inferred.modules:
        lines.append(
            "central modules: "
            + ", ".join(
                f"{module} ({layer})" for module, layer in inferred.modules.items()
            )
        )
    if inferred.unclassified_roots:
        lines.append(
            f"no layer yet: {', '.join(inferred.unclassified_roots)} "
            "(see architecture.modules)"
        )
    mirror = inferred.mirror
    if mirror is None:
        lines.append("tests: no test mirrors a module yet, mirroring is off")
    else:
        lines.append(
            f"tests: mirror {mirror.pattern} "
            f"({mirror.mirrored} of {mirror.total} test files already mirror)"
        )
    return lines


def _violation_summary(path: Path) -> str:
    try:
        outcome = run_check(load_config(path), CheckOptions(use_debt=False))
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
        "`smelt check`, or `smelt debt` to adopt incrementally."
    )


DEFAULT_DEBT = ".smelt/debt.json"


def debt(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    loaded = load_project_config(args.config, cwd)
    configured = loaded.config.debt
    relative = configured or DEFAULT_DEBT
    path = loaded.root / relative
    outcome = run_check(loaded, CheckOptions(use_debt=False))
    snippet = snippet_reader(outcome.context)
    current = [
        v
        for v in outcome.unfiltered
        if v.code != "SMT903" and v.severity is not Severity.HINT
    ]
    if args.prune:
        existing = Debt.load(path)
        _, _, resolved = existing.match(current, snippet)
        existing.without(resolved).write(path)
        remaining = len(existing.entries) - len(resolved)
        console.print(
            f"Removed {len(resolved)} resolved entr{'y' if len(resolved) == 1 else 'ies'} "
            f"from {relative} ({remaining} remain)"
        )
    else:
        Debt.from_violations(current, snippet).write(path)
        console.print(
            f"Wrote {relative} ({len(current)} violation{'s' * (len(current) != 1)})"
        )
    if configured is None:
        _enable_debt(loaded.path, relative)
        console.print(f"Added `debt: {relative}` to {loaded.path.name}")
    return EXIT_OK


def _enable_debt(config: Path, relative: str) -> None:
    """Set the debt key, uncommenting the line `smelt init` leaves if it is there."""
    text = config.read_text(encoding="utf-8")
    line = f"debt: {relative}"
    lines = text.splitlines()
    for index, existing in enumerate(lines):
        if existing.strip() in (f"# {line}", f"#{line}"):
            lines[index] = line
            config.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            return
    separator = "" if not text or text.endswith("\n") else "\n"
    config.write_text(f"{text}{separator}{line}\n", encoding="utf-8", newline="\n")
