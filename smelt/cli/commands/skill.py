"""Install the release's offline agent instructions without loading project policy."""

from __future__ import annotations

import shutil
import tempfile
from importlib.resources import as_file, files
from pathlib import Path
from typing import TYPE_CHECKING

from smelt import __version__
from smelt.cli.support import EXIT_OK, CliError, Console

if TYPE_CHECKING:
    import argparse


def skill_install(args: argparse.Namespace, console: Console, cwd: Path) -> int:
    destination = cwd / args.destination
    _validate_destination(destination, force=args.force)
    destination = destination.resolve()
    with as_file(files("smelt").joinpath("skills", "smelt")) as resource:
        source = resource.resolve()
        if destination.is_relative_to(source) or source.is_relative_to(destination):
            raise CliError("skill destination must not overlap the bundled skill")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=".smelt-skill-", dir=destination.parent)
        )
        staged, backup = temporary / "skill", temporary / "previous"
        try:
            shutil.copytree(source, staged)
            (staged / "VERSION").write_text(__version__ + "\n", encoding="utf-8")
            _validate_destination(destination, force=args.force)
            if destination.exists():
                destination.rename(backup)
            try:
                staged.rename(destination)
            except OSError:
                if backup.exists():
                    backup.rename(destination)
                raise
            if backup.exists():
                shutil.rmtree(backup)
        finally:
            # If rollback itself fails, retain the backup for manual recovery.
            if not backup.exists():
                shutil.rmtree(temporary)
    console.print(f"Installed Smelt skill {__version__} in {destination}")
    return EXIT_OK


def _validate_destination(destination: Path, *, force: bool) -> None:
    if destination.is_symlink() or destination.is_junction():
        raise CliError(f"refusing to replace a linked skill directory: {destination}")
    if not destination.exists():
        return
    if not force:
        raise CliError(
            f"skill destination already exists: {destination}; "
            "use --force to replace it (including local edits)"
        )
    if not destination.is_dir() or not (destination / "SKILL.md").is_file():
        raise CliError(f"refusing to replace a non-skill path: {destination}")
