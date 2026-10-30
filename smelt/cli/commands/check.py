from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from smelt import __version__
from smelt.analysis.files import FileIndex
from smelt.cli.support import (
    EXIT_OK,
    EXIT_VIOLATIONS,
    CliError,
    Console,
    load_project_config,
    use_color,
)
from smelt.diagnostics.render.machine import render_github, render_json, render_sarif
from smelt.diagnostics.render.text import render_text
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions, relative_scope, run_check
from smelt.engine.ordering_fixes import apply_ordering_fixes
from smelt.engine.paths import project_metadata
from smelt.engine.staged import staged_project

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from smelt.config import LoadedConfig
    from smelt.engine.staged import StagedProject


def split_selectors(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(code.strip() for code in raw.split(",") if code.strip())


def _scope(loaded: LoadedConfig, cwd: Path, raw: list[str]) -> tuple[str, ...]:
    paths = tuple(relative_scope(loaded.root, cwd, p) for p in raw)
    # A changed config can break any file, so it widens the scope to the project.
    config = relative_scope(loaded.root, cwd, str(loaded.path))
    return () if config in paths else paths


def check(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    if args.staged:
        if args.fix:
            raise CliError(
                "--staged cannot be combined with --fix; fix and stage the working files first",
                option="--fix",
                value="--staged",
            )
        with staged_project(cwd, args.config) as staged:
            return _check_loaded(
                args,
                console,
                cwd,
                staged.loaded,
                staged=staged,
            )
    loaded = load_project_config(args.config, cwd)
    return _check_loaded(args, console, cwd, loaded)


def _check_loaded(
    args: argparse.Namespace,
    console: Console,
    cwd: Path,
    loaded: LoadedConfig,
    *,
    staged: StagedProject | None = None,
) -> int:
    original = staged.original_config if staged else None
    index_hash = staged.index_hash if staged else None
    origin = replace(loaded, path=original) if original else loaded
    options = CheckOptions(
        strict_coverage=args.strict_coverage,
        show_coverage=args.show_coverage,
        paths=_scope(origin, cwd, args.paths),
        changed=args.changed or args.staged or args.base is not None,
        base=args.base,
        select=split_selectors(args.select),
        ignore=split_selectors(args.ignore),
        fail_on=Severity(args.fail_on),
        use_debt=not args.no_debt,
        use_cache=not args.no_cache,
        cache_dir=origin.root / ".smelt_cache",
        comparison_root=origin.root if args.staged else None,
        staged=args.staged,
        snapshot_id=index_hash,
        repository_root=staged.repository if staged else None,
    )
    outcome = run_check(loaded, options)
    if args.fix:
        fixes = apply_ordering_fixes(outcome)
        if fixes.files:
            outcome = run_check(loaded, options)
        outcome.report.fixes = {"ordering": fixes.to_json()}
        if args.format == "text":
            console.print(
                f"Ordering fixes: {len(fixes.files)} file(s), {fixes.sections} section(s); {len(fixes.skipped)} skipped."
            )
    report = outcome.report
    if args.staged:
        report.project = project_metadata(origin)
        if report.comparison is not None:
            report.comparison = {**report.comparison, "index_hash": index_hash}
    files = outcome.context.__dict__.get("files") or FileIndex(
        outcome.context.root, outcome.context.config
    )

    def read_line(path: str, line: int) -> str:
        try:
            return files.line(path, line)
        except OSError:
            return ""

    match args.format:
        case "json":
            console.print(render_json(report), end="")
        case "sarif":
            console.print(render_sarif(report, __version__), end="")
        case "github":
            console.print(render_github(report), end="")
        case _:
            color = use_color(console.out, disabled=args.no_color)
            text = render_text(
                report,
                read_line,
                color=color,
                # Hints on the files just changed are few and relevant.
                show_hints=args.show_hints or options.changed,
                statistics=args.statistics,
            )
            console.print(text, end="")
            if args.show_coverage and report.coverage:
                console.print("Coverage inventory:")
                for entry in report.coverage.get("inventory", []):
                    console.print(
                        f"  {entry['status']}: {entry['path']} — {entry['reason']}"
                    )
    return EXIT_VIOLATIONS if report.failed else EXIT_OK
