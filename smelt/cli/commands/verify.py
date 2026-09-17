from __future__ import annotations

import json
from typing import TYPE_CHECKING

from smelt.cli.support import (
    EXIT_OK,
    EXIT_VIOLATIONS,
    CliError,
    Console,
    load_project_config,
)
from smelt.engine.verify import StepResult, run_steps

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

OUTPUT_TAIL = 20

MARKS = {"passed": "✓", "failed": "✗", "skipped": "-"}


def _print_result(console: Console, result: StepResult) -> None:
    timing = f" ({result.duration:.1f}s)" if result.status != "skipped" else ""
    console.print(f"{MARKS[result.status]} {result.name}{timing}")
    if result.status == "failed":
        lines = result.output.rstrip().splitlines()[-OUTPUT_TAIL:]
        for line in lines:
            console.print(f"    {line}")


def verify(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    loaded = load_project_config(args.config, cwd)
    steps = loaded.config.verify
    if not steps:
        msg = f"no verify steps configured; add a `verify:` list to {loaded.path.name}"
        raise CliError(msg)
    text = args.format == "text"
    results = run_steps(
        steps,
        loaded.root,
        fail_fast=args.fail_fast,
        on_result=(lambda result: _print_result(console, result)) if text else None,
    )
    failed = [result.name for result in results if result.status == "failed"]
    if text:
        passed = sum(result.status == "passed" for result in results)
        summary = f"{passed}/{len(results)} steps passed"
        console.print(
            f"\n{summary}" + (f"; failed: {', '.join(failed)}" if failed else "")
        )
    else:
        data = {
            "schema_version": 1,
            "passed": not failed,
            "steps": [result.to_json() for result in results],
        }
        console.print(json.dumps(data, indent=2))
    return EXIT_VIOLATIONS if failed else EXIT_OK
