from typing import TYPE_CHECKING

from smelt.cli.support import CliError, Console

if TYPE_CHECKING:
    import argparse
    from pathlib import Path


def _pending(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    msg = f"`smelt {args.command}` is not implemented yet"
    raise CliError(msg)


fix = _pending
