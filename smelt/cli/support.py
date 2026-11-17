from __future__ import annotations

import contextlib
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from smelt.config.errors import ConfigError, ConfigIssue

if TYPE_CHECKING:
    from smelt.config.loader import LoadedConfig

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2


class CliError(Exception):
    """A usage or environment problem; reported with exit code 2.

    ``kind``, ``option`` and ``value`` end up in the JSON error document;
    ``option`` and ``value`` name the argument the user got wrong, if there is one.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "usage",
        option: str | None = None,
        value: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.option = option
        self.value = value


@dataclass(slots=True)
class Console:
    out: TextIO
    err: TextIO

    def print(self, text: str = "", *, end: str = "\n") -> None:
        self.out.write(text + end)

    def error(self, text: str) -> None:
        self.err.write(f"error: {text}\n")

    def warn(self, text: str) -> None:
        self.err.write(f"warning: {text}\n")


def use_color(stream: TextIO, *, disabled: bool) -> bool:
    if disabled or os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def load_project_config(config_path: str | None, cwd: Path) -> LoadedConfig:
    from smelt.config.loader import (  # noqa: PLC0415 - keep schemas out of CLI startup
        discover_config,
        load_config,
    )

    if config_path:
        path = Path(config_path)
        if not path.is_absolute():
            path = cwd / path
        if not path.is_file():
            raise ConfigError(
                [ConfigIssue("", f"config file not found: {config_path}")]
            )
        return load_config(path)
    found = discover_config(cwd)
    if found is None:
        msg = "no smelt.yml found in this directory or its parents (run `smelt init`)"
        raise CliError(msg, kind="config")
    loaded = load_config(found)
    if loaded.config.project.workspace == "uv":
        from smelt.config.workspace import member_directories  # noqa: PLC0415

        owners = [
            m.path
            for m in member_directories(loaded.root)
            if m.skipped is None
            and cwd.resolve().is_relative_to((loaded.root / m.path).resolve())
        ]
        if owners:
            loaded = replace(loaded, member=max(owners, key=len))
    return loaded


def configure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")
