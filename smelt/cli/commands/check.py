from __future__ import annotations

from typing import TYPE_CHECKING

from smelt import __version__
from smelt.cli.support import (
    EXIT_OK,
    EXIT_VIOLATIONS,
    Console,
    load_project_config,
    use_color,
)
from smelt.diagnostics.render.machine import render_github, render_json, render_sarif
from smelt.diagnostics.render.text import render_text
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions, relative_scope, run_check
from smelt.engine.ordering_fixes import apply_ordering_fixes

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from smelt.config import LoadedConfig


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
    loaded = load_project_config(args.config, cwd)
    options = CheckOptions(
        strict_coverage=args.strict_coverage,
        show_coverage=args.show_coverage,
        paths=_scope(loaded, cwd, args.paths),
        changed=args.changed or args.base is not None,
        base=args.base,
        select=split_selectors(args.select),
        ignore=split_selectors(args.ignore),
        fail_on=Severity(args.fail_on),
        use_debt=not args.no_debt,
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
    files = outcome.context.files

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
