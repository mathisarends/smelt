"""Load only the command being executed, after argument parsing and cache lookup."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable
    from pathlib import Path

    from smelt.cli.commands.adopt import debt, init
    from smelt.cli.commands.check import check
    from smelt.cli.commands.discover import context
    from smelt.cli.commands.info import config_schema, config_show, explain, rules
    from smelt.cli.support import Console


__all__ = [
    "check",
    "config_schema",
    "config_show",
    "context",
    "debt",
    "explain",
    "handler",
    "init",
    "rules",
]


def handler(name: str) -> Callable[[argparse.Namespace, Console, Path], int]:
    """Resolve command imports only when the parsed handler is called."""

    def dispatch(args: argparse.Namespace, console: Console, cwd: Path) -> int:
        target = __getattr__(name)
        return int(target(args, console, cwd))

    return dispatch


def __getattr__(name: str) -> Any:
    if name not in _MODULES:
        raise AttributeError(name)
    target = getattr(import_module(f"smelt.cli.commands.{_MODULES[name]}"), name)
    # Importing the check submodule also assigns package.check to the module.
    # Preserve the previously exported callable after either import path.
    globals()[name] = target
    return target


_MODULES = {
    "check": "check",
    "config_schema": "info",
    "config_show": "info",
    "context": "discover",
    "debt": "adopt",
    "explain": "info",
    "init": "adopt",
    "rules": "info",
}
