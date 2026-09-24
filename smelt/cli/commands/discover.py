from __future__ import annotations

import json
from typing import TYPE_CHECKING

from smelt.cli.support import EXIT_OK, CliError, Console, load_project_config
from smelt.engine.architecture_map import build_architecture_map
from smelt.engine.briefing import (
    TargetError,
    build_briefing,
    render_briefing,
    resolve_target,
)
from smelt.engine.check import CheckOptions, relative_scope, run_check

if TYPE_CHECKING:
    import argparse
    from pathlib import Path


def context(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    loaded = load_project_config(args.config, cwd)
    outcome = run_check(loaded, CheckOptions())
    ctx = outcome.context
    raw = args.target
    if raw is not None and raw not in ctx.model.features:
        raw = relative_scope(loaded.root, cwd, raw)
    try:
        target = resolve_target(ctx, raw)
    except TargetError as exc:
        raise CliError(str(exc)) from exc
    briefing = build_briefing(ctx, target, outcome.report)
    if args.format == "json":
        console.print(json.dumps(briefing.to_json(), indent=2))
    else:
        console.print(render_briefing(briefing, ctx), end="")
    return EXIT_OK


def inspect(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    loaded = load_project_config(args.config, cwd)
    outcome = run_check(loaded, CheckOptions())
    data = build_architecture_map(outcome.context, outcome.report)
    console.print(json.dumps(data, indent=2))
    return EXIT_OK
