import json
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext
from smelt.cli.support import EXIT_OK, CliError, Console, load_project_config
from smelt.config.errors import did_you_mean
from smelt.engine.architecture_map import build_architecture_map
from smelt.engine.briefing import (
    TargetError,
    build_briefing,
    layer_path,
    render_briefing,
    resolve_target,
    where_path,
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


def where(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    loaded = load_project_config(args.config, cwd)
    ctx = AnalysisContext(loaded.root, loaded.config)
    model = ctx.model
    config = loaded.config
    feature: str | None = args.feature
    if model.has_features and feature is None:
        known = ", ".join(sorted(model.features)) or "none yet"
        msg = f"--feature is required (features: {known})"
        raise CliError(msg)
    if not model.has_features and feature is not None:
        msg = "--feature given, but architecture.features is not configured"
        raise CliError(msg)
    if feature is not None and feature not in model.features:
        console.warn(f'feature "{feature}" does not exist yet')

    if args.role in config.roles:
        if not config.roles[args.role].layers:
            msg = f'role "{args.role}" has no layers configured'
            raise CliError(msg)
        path = where_path(ctx, args.role, feature)
    elif args.role in model.layers:
        path = layer_path(ctx, args.role, feature)
    else:
        candidates = [*config.roles, *model.layers]
        msg = f'unknown role "{args.role}"{did_you_mean(args.role, candidates)}'
        raise CliError(msg)
    if path is None:
        msg = f'no canonical location for "{args.role}"'
        raise CliError(msg)
    console.print(path)
    return EXIT_OK


def inspect(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    loaded = load_project_config(args.config, cwd)
    outcome = run_check(loaded, CheckOptions())
    data = build_architecture_map(outcome.context, outcome.report)
    console.print(json.dumps(data, indent=2))
    return EXIT_OK
