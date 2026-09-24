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

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from smelt.config import LoadedConfig


def split_codes(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(code.strip().upper() for code in raw.split(",") if code.strip())


def _scope(loaded: LoadedConfig, cwd: Path, raw: list[str]) -> tuple[str, ...]:
    paths = tuple(relative_scope(loaded.root, cwd, p) for p in raw)
    # A changed config can break any file, so it widens the scope to the project.
    config = relative_scope(loaded.root, cwd, str(loaded.path))
    return () if config in paths else paths


def check(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    loaded = load_project_config(args.config, cwd)
    if args.format == "text":
        for warning in loaded.warnings:
            console.warn(str(warning))
    options = CheckOptions(
        paths=_scope(loaded, cwd, args.paths),
        changed=args.changed or args.base is not None,
        base=args.base,
        select=split_codes(args.select),
        ignore=split_codes(args.ignore),
        fail_on=Severity(args.fail_on),
        use_baseline=not args.no_baseline,
    )
    outcome = run_check(loaded, options)
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
                report, read_line, color=color, show_hints=args.show_hints
            )
            console.print(text, end="")
    return EXIT_VIOLATIONS if report.failed else EXIT_OK
