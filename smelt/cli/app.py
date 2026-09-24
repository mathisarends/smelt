import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from smelt import __version__
from smelt.analysis.parsing import AnalysisError
from smelt.cli import commands
from smelt.cli.support import (
    EXIT_ERROR,
    CliError,
    Console,
    configure_streams,
)
from smelt.config import ConfigError
from smelt.rules.registry import PluginError

type Handler = Callable[[argparse.Namespace, Console, Path], int]


type Subparsers = argparse._SubParsersAction[argparse.ArgumentParser]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smelt",
        description="Static guardrails for Python architecture.",
    )
    parser.add_argument("--version", action="version", version=f"smelt {__version__}")
    parser.add_argument("--config", metavar="PATH", help="path to smelt.yaml")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    _add_check(sub)
    _add_discovery(sub)
    _add_maintenance(sub)
    _add_config(sub)
    return parser


def _add_check(sub: Subparsers) -> None:
    check = sub.add_parser("check", help="check the project against smelt.yaml")
    check.add_argument(
        "paths", nargs="*", metavar="PATHS", help="only report these files"
    )
    check.add_argument(
        "--changed", action="store_true", help="only report changed files"
    )
    check.add_argument("--base", metavar="REF", help="compare --changed against REF")
    check.add_argument(
        "--format", choices=["text", "json", "sarif", "github"], default="text"
    )
    check.add_argument("--fail-on", choices=["error", "warning"], default="error")
    check.add_argument(
        "--select", metavar="CODES", help="comma-separated code prefixes"
    )
    check.add_argument(
        "--ignore", metavar="CODES", help="comma-separated code prefixes"
    )
    check.add_argument(
        "--show-hints", action="store_true", help="list hints in text output"
    )
    check.add_argument("--no-color", action="store_true")
    check.add_argument("--no-baseline", action="store_true", help="ignore the baseline")
    check.set_defaults(handler=commands.check)


def _add_discovery(sub: Subparsers) -> None:
    explain = sub.add_parser("explain", help="explain a rule")
    explain.add_argument("rule", metavar="CODE|NAME")
    explain.add_argument("--format", choices=["text", "json"], default="text")
    explain.set_defaults(handler=commands.explain)

    rules = sub.add_parser("rules", help="list all rules")
    rules.add_argument("--format", choices=["text", "json"], default="text")
    rules.set_defaults(handler=commands.rules)

    context = sub.add_parser(
        "context", help="architecture briefing for a feature or path"
    )
    context.add_argument("target", nargs="?", metavar="FEATURE|PATH")
    context.add_argument("--format", choices=["text", "json"], default="text")
    context.set_defaults(handler=commands.context)

    inspect = sub.add_parser("inspect", help="machine-readable architecture map")
    inspect.add_argument("--format", choices=["json"], default="json")
    inspect.set_defaults(handler=commands.inspect)


def _add_maintenance(sub: Subparsers) -> None:
    init = sub.add_parser("init", help="write a starter smelt.yaml")
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    init.set_defaults(handler=commands.init)

    baseline = sub.add_parser(
        "baseline", help="write the baseline of current violations"
    )
    baseline.add_argument(
        "--prune", action="store_true", help="only remove stale entries"
    )
    baseline.set_defaults(handler=commands.baseline)

    verify = sub.add_parser("verify", help="run the configured verification stack")
    verify.add_argument("--format", choices=["text", "json"], default="text")
    verify.add_argument("--fail-fast", action="store_true")
    verify.set_defaults(handler=commands.verify)


def _add_config(sub: Subparsers) -> None:
    config = sub.add_parser("config", help="show the resolved config or its schema")
    config_sub = config.add_subparsers(dest="config_command", metavar="ACTION")
    show = config_sub.add_parser("show", help="print the resolved config")
    show.add_argument("--format", choices=["yaml", "json"], default="yaml")
    show.set_defaults(handler=commands.config_show)
    schema = config_sub.add_parser("schema", help="print the JSON schema")
    schema.set_defaults(handler=commands.config_schema)


def main(argv: Sequence[str] | None = None) -> int:
    configure_streams()
    console = Console(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Handler | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return EXIT_ERROR
    try:
        return handler(args, console, Path.cwd())
    except ConfigError as exc:
        console.err.write(f"{exc}\n")
    except (AnalysisError, CliError, PluginError) as exc:
        console.error(str(exc))
    except json.JSONDecodeError as exc:
        console.error(f"invalid JSON: {exc}")
    return EXIT_ERROR
