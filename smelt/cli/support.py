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


def use_color(stream: TextIO, *, disabled: bool) -> bool:
    if disabled or os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def load_project_config(config_path: str | None, cwd: Path) -> LoadedConfig:
    from smelt.config.loader import (  # noqa: PLC0415 - keep schemas out of CLI startup
        discover_config,
    )

    if config_path:
        path = Path(config_path)
        if not path.is_absolute():
            path = cwd / path
        if not path.is_file():
            raise ConfigError(
                [ConfigIssue("", f"config file not found: {config_path}")]
            )
        return _load_reporting_selectors(path)
    found = discover_config(cwd)
    if found is None:
        msg = "no smelt.yml found in this directory or its parents (run `smelt init`)"
        raise CliError(msg, kind="config")
    loaded = _load_reporting_selectors(found)
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


def _load_reporting_selectors(path: Path) -> LoadedConfig:
    """Load a config; schema errors also list unknown rule selectors at once."""
    from smelt.config.loader import load_config, raw_config  # noqa: PLC0415
    from smelt.engine.rule_settings import unknown_selectors  # noqa: PLC0415
    from smelt.rules.registry import build_rule_set  # noqa: PLC0415

    try:
        return load_config(path)
    except ConfigError as exc:
        raw = raw_config(path)
        if "version" not in raw:
            raise
        reported = {issue.path for issue in exc.issues}
        missing = [
            issue
            for issue in unknown_selectors(raw, build_rule_set(lazy=True))
            if issue.path not in reported
        ]
        if not missing:
            raise
        raise ConfigError(
            [
                *(i for i in exc.issues if not i.semantic),
                *missing,
                *(i for i in exc.issues if i.semantic),
            ],
            exc.source,
        ) from exc
