from __future__ import annotations

from pathlib import Path
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

    from smelt.engine.inference import InferredConfig
    from smelt.engine.workspace import WorkspaceMember


def init(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    custom: str | None = args.config
    target, shown = _init_target(custom, cwd)
    if target.is_dir():
        msg = f"{shown} is a directory; pass the path of the config file to write"
        raise CliError(msg, option="--config", value=custom)
    if target.exists() and not args.force:
        msg = f"{shown} already exists (use --force to overwrite)"
        raise CliError(msg, option="--config" if custom else None, value=custom)
    root = target.parent
    if not root.is_dir():
        msg = (
            f"directory {root} does not exist; it would be the project root of {shown}"
        )
        raise CliError(msg, option="--config", value=custom)
    inferred = infer_config(root)
    if inferred is None:
        msg = (
            "no Python package found in this directory, src/, or declared uv workspace members; "
            "run `smelt init` from the project root or configure source_roots manually"
        )
        raise CliError(msg)
    target.write_text(render_config(inferred), encoding="utf-8", newline="\n")
    console.print(f"Wrote {shown}")
    for line in _summary(inferred):
        console.print(f"  {line}")
    console.print(_violation_summary(target))
    if target.name not in CONFIG_FILENAMES:
        console.print(
            f"smelt only finds {' or '.join(CONFIG_FILENAMES)} on its own; "
            f"pass --config {shown} to every command."
        )
    return EXIT_OK


def _init_target(custom: str | None, cwd: Path) -> tuple[Path, str]:
    """The one file ``init`` writes: ``--config``, else the default config here.

    Only that file is checked and overwritten; a reviewed config elsewhere stays.
    Without ``--config`` an existing ``smelt.yml`` is the target, not a second file.
    """
    if custom is not None:
        path = Path(custom)
        return (path if path.is_absolute() else cwd / path), custom
    existing = next((name for name in CONFIG_FILENAMES if (cwd / name).exists()), None)
    name = existing or CONFIG_FILENAME
    return cwd / name, name


def _summary(inferred: InferredConfig) -> list[str]:
    lines = [f"root packages: {', '.join(inferred.root_packages)}"]
    if inferred.members:
        lines.append("workspace members:")
        lines.extend(f"  {_member_status(member)}" for member in inferred.members)
    lines.append(
        "boundary coverage: direct imports only; third-party packages allowed (review layers.*.third_party and imports.transitive)"
    )
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
        if mirror.name_clashes:
            lines.append(
                f"pytest: {', '.join(mirror.name_clashes)} "
                f"{'has' if len(mirror.name_clashes) == 1 else 'have'} no __init__.py, so two "
                "mirrored test_router.py files clash; add --import-mode=importlib to "
                "the pytest addopts"
            )
    return lines


def _member_status(member: WorkspaceMember) -> str:
    if member.skipped:
        return f"{member.path}: skipped, {member.skipped}"
    packages = ", ".join(
        f"{name} (namespace package)" if name in member.namespace else name
        for name in member.packages
    )
    return f"{member.path}: {packages} in {member.source_root}"


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
        f"{warnings} warning{'s' * (warnings != 1)}. Review {path.name}, then run "
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
        _, known, resolved = existing.match(current, snippet)
        # Upgrade legacy import fingerprints without accepting any new debt.
        Debt.from_violations(known, snippet).write(path)
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
