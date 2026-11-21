from __future__ import annotations

from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING

from smelt import __version__
from smelt.analysis.cache import cache_directory
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
from smelt.diagnostics.render.text import render_concise, render_text
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions, relative_scope, run_check
from smelt.engine.fixes import apply_fixes, fix_diff
from smelt.engine.paths import project_metadata
from smelt.engine.staged import staged_project

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from smelt.config import LoadedConfig
    from smelt.engine.staged import StagedProject


def check(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    if args.by != "rule" and not args.statistics:
        raise CliError(
            "--by member requires --statistics", option="--by", value=args.by
        )
    if args.fix and args.diff:
        raise CliError(
            "--diff previews the fixes without writing; use either --fix or --diff",
            option="--diff",
            value="--fix",
        )
    if args.staged:
        if args.fix or args.diff:
            option = "--fix" if args.fix else "--diff"
            raise CliError(
                f"--staged cannot be combined with {option}; fix and stage the working files first",
                option=option,
                value="--staged",
            )
        with staged_project(
            cwd,
            args.config,
            index=getattr(args, "_staged_index", None),
            use_cache=not args.no_cache,
        ) as staged:
            if staged.loaded.source is not None:
                args._staged_inputs = staged.loaded.source.dependencies()
            return _check_loaded(
                args,
                console,
                cwd,
                loaded=staged.loaded,
                staged=staged,
            )
    loaded = load_project_config(args.config, cwd)
    return _check_loaded(args, console, cwd, loaded=loaded)


def _check_loaded(
    args: argparse.Namespace,
    console: Console,
    cwd: Path,
    *,
    loaded: LoadedConfig,
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
        select=_split_selectors(args.select),
        ignore=_split_selectors(args.ignore),
        fail_on=Severity(args.fail_on),
        use_debt=not args.no_debt,
        use_cache=not args.no_cache,
        cache_dir=cache_directory(origin.root),
        comparison_root=origin.root if args.staged else None,
        staged=args.staged,
        snapshot_id=index_hash,
        repository_root=staged.repository if staged else None,
        comparison=getattr(args, "_comparison", None),
    )
    outcome = run_check(loaded, options)
    if args.diff:
        diff = fix_diff(outcome, unsafe=args.unsafe_fixes)
        console.print(diff, end="")
        return EXIT_VIOLATIONS if diff else EXIT_OK
    if args.fix:
        outcome, fixes = apply_fixes(
            outcome, partial(run_check, loaded, options), unsafe=args.unsafe_fixes
        )
        outcome.report.fixes = fixes.to_json()
        if args.format == "text":
            console.print(
                f"Fixed {_plural(fixes.applied, 'finding')} in {_plural(len(fixes.files), 'file')}; {len(fixes.skipped)} skipped."
            )
    report = outcome.report
    if args.staged:
        report.project = project_metadata(origin)
        if report.comparison is not None:
            report.comparison = {**report.comparison, "index_hash": index_hash}
    files = outcome.context.__dict__.get("files") or FileIndex(
        outcome.context.root, outcome.context.config, store=outcome.context.store
    )

    def read_line(path: str, line: int) -> str:
        try:
            return files.line(path, line)
        except OSError:
            return ""

    match args.format:
        case "json":
            console.print(render_json(report, show_coverage=args.show_coverage), end="")
        case "sarif":
            console.print(render_sarif(report, __version__), end="")
        case "github":
            console.print(render_github(report), end="")
        case "concise":
            console.print(
                render_concise(
                    report,
                    show_hints=args.show_hints or options.changed,
                    statistics=args.statistics,
                    by=args.by,
                ),
                end="",
            )
        case _:
            color = use_color(console.out, disabled=args.no_color)
            text = render_text(
                report,
                read_line,
                color=color,
                # Hints on the files just changed are few and relevant.
                show_hints=args.show_hints or options.changed,
                statistics=args.statistics,
                by=args.by,
            )
            console.print(text, end="")
            if args.show_coverage and report.coverage:
                console.print("Coverage inventory:")
                for entry in report.coverage.get("inventory", []):
                    console.print(
                        f"  {entry['status']}: {entry['path']} — {entry['reason']}"
                    )
    return EXIT_VIOLATIONS if report.failed else EXIT_OK


def _split_selectors(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(code.strip() for code in raw.split(",") if code.strip())


def _scope(loaded: LoadedConfig, cwd: Path, raw: list[str]) -> tuple[str, ...]:
    # Every inherited policy input can affect unchanged production files too.
    from smelt.config.source import SourceStore  # noqa: PLC0415

    store = loaded.source or SourceStore()
    if not raw and loaded.member is not None:
        # Started in a workspace member: report that member's files only.
        return (loaded.member,)
    if any(store.resolve(cwd / path) in loaded.inputs for path in raw):
        return ()
    paths = tuple(relative_scope(loaded.root, cwd, p, store=loaded.source) for p in raw)
    # A changed config can break any file, so it widens the scope to the project.
    config = relative_scope(loaded.root, cwd, str(loaded.path), store=loaded.source)
    return () if config in paths else paths


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"
