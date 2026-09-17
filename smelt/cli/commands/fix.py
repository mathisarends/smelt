from typing import TYPE_CHECKING

from smelt.cli.support import EXIT_OK, Console, load_project_config
from smelt.engine.fix import run_fix
from smelt.fix import render_diff

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from smelt.engine.fix import FixOutcome


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _report_skipped(console: Console, outcome: FixOutcome) -> None:
    for violation in outcome.skipped:
        where = (
            f"{violation.path}:{violation.line}" if violation.path else violation.code
        )
        console.warn(
            f"{where}: {violation.code} overlaps another fix; run `smelt fix` again"
        )


def fix(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    loaded = load_project_config(args.config, cwd)
    codes = tuple(args.codes)
    outcome = run_fix(loaded, codes, dry_run=args.dry_run)
    if not outcome.applied:
        console.print("no fixable violations found")
        _report_skipped(console, outcome)
        return EXIT_OK

    if args.dry_run:
        for diff in outcome.diffs:
            console.print(render_diff(diff))
        console.print(
            f"\nwould apply {_plural(outcome.fixes, 'fix')} "
            f"in {_plural(len(outcome.diffs), 'file')}"
        )
    else:
        for violation in outcome.applied:
            if violation.fix is not None:
                console.print(f"{violation.code} {violation.fix.description}")
        files = {diff.path for diff in outcome.diffs}
        console.print(
            f"\napplied {_plural(outcome.fixes, 'fix')} in {_plural(len(files), 'file')}"
        )
    _report_skipped(console, outcome)
    return EXIT_OK
