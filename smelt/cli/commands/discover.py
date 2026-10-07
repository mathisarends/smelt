from __future__ import annotations

import json
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext
from smelt.analysis.parsing import AnalysisError
from smelt.cli.support import EXIT_OK, CliError, Console, load_project_config
from smelt.config import ConfigError
from smelt.engine.briefing import (
    TargetError,
    build_briefing,
    render_briefing,
    resolve_target,
)
from smelt.engine.check import CheckOptions, relative_scope, run_check
from smelt.engine.paths import missing_paths

if TYPE_CHECKING:
    import argparse
    from pathlib import Path


def context(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    loaded = load_project_config(args.config, cwd)
    ctx = AnalysisContext(loaded.root, loaded.config)
    issues = missing_paths(ctx)
    if issues:
        raise ConfigError(issues, loaded.path.name)
    raw = args.target
    if (
        raw is not None
        and raw not in ctx.model.features
        and ctx.files.path_for_module(raw) is None
    ):
        raw = relative_scope(loaded.root, cwd, raw)
    try:
        target = resolve_target(ctx, raw)
    except TargetError as exc:
        raise CliError(str(exc), option="target", value=args.target) from exc
    try:
        outcome = run_check(loaded, CheckOptions(), context=ctx)
    except (AnalysisError, ConfigError) as exc:
        briefing = build_briefing(ctx, target, None, analysis_error=str(exc))
    else:
        briefing = build_briefing(ctx, target, outcome.report)
    if args.format == "json":
        console.print(json.dumps(briefing.to_json(), indent=2))
    else:
        console.print(render_briefing(briefing, ctx), end="")
    return EXIT_OK
