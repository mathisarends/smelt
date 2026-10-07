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
from smelt.diagnostics.render.machine import render_error_json
from smelt.engine.check import InvalidOptionError

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
    for command in sub.choices.values():
        _allow_local_config(command)
    return parser


def _allow_local_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config", metavar="PATH", default=argparse.SUPPRESS, help="path to smelt.yaml"
    )
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                _allow_local_config(child)


def _add_check(sub: Subparsers) -> None:
    check = sub.add_parser("check", help="check the project against smelt.yaml")
    check.add_argument(
        "paths", nargs="*", metavar="PATHS", help="only report these files"
    )
    check.add_argument(
        "--changed",
        action="store_true",
        help="only report violations introduced since HEAD (incl. uncommitted files)",
    )
    check.add_argument(
        "--base",
        metavar="REF",
        help="compare --changed against the merge-base with REF",
    )
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
        "--show-hints",
        action="store_true",
        help="list hints in text output (always on with --changed)",
    )
    check.add_argument("--no-color", action="store_true")
    check.add_argument("--no-debt", action="store_true", help="ignore the debt file")
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
        "context",
        help="architecture briefing for a feature, module or planned source path",
    )
    context.add_argument("target", nargs="?", metavar="FEATURE|MODULE|PATH")
    context.add_argument("--format", choices=["text", "json"], default="text")
    context.set_defaults(handler=commands.context)


def _add_maintenance(sub: Subparsers) -> None:
    init = sub.add_parser("init", help="write a starter smelt.yaml")
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    init.set_defaults(handler=commands.init)

    debt = sub.add_parser("debt", help="record current violations as known debt")
    debt.add_argument(
        "--prune", action="store_true", help="only remove resolved entries"
    )
    debt.set_defaults(handler=commands.debt)


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
    except (AnalysisError, CliError, ConfigError, json.JSONDecodeError) as exc:
        if getattr(args, "format", None) == "json":
            console.print(_error_json(exc), end="")
        elif isinstance(exc, ConfigError):
            console.err.write(f"{exc}\n")
        else:
            console.error(_message(exc))
    return EXIT_ERROR


def _message(exc: Exception) -> str:
    return f"invalid JSON: {exc}" if isinstance(exc, json.JSONDecodeError) else str(exc)


def _error_json(exc: Exception) -> str:
    """Errors in the format the agent asked for, so it needs no text parser."""
    if isinstance(exc, ConfigError):
        header, *_ = str(exc).splitlines()
        issues = [
            {"location": i.path or None, "message": i.message} for i in exc.issues
        ]
        return render_error_json(
            "config", header, config={"file": exc.source, "issues": issues}
        )
    kind, option, value = "analysis", None, None
    if isinstance(exc, CliError):
        kind, option, value = exc.kind, exc.option, exc.value
    elif isinstance(exc, InvalidOptionError):
        kind, option, value = "usage", exc.option, exc.value
    given = (
        {"option": option, "value": value}
        if option is not None and value is not None
        else None
    )
    return render_error_json(kind, _message(exc), invalid_input=given)
