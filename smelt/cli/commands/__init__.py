"""Load only the command being executed, after argument parsing and cache lookup."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable
    from pathlib import Path

    from smelt.cli.support import Console

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


def handler(name: str) -> Callable[[argparse.Namespace, Console, Path], int]:
    """Resolve command imports only when the parsed handler is called."""

    def dispatch(args: argparse.Namespace, console: Console, cwd: Path) -> int:
        return int(
            getattr(import_module(f"smelt.cli.commands.{_MODULES[name]}"), name)(
                args, console, cwd
            )
        )

    return dispatch


__all__ = ["handler"]
