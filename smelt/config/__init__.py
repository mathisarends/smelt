from importlib import import_module
from typing import TYPE_CHECKING, Any

from smelt.config.errors import ConfigError, ConfigIssue

if TYPE_CHECKING:
    from smelt.config.loader import (
        CONFIG_FILENAME,
        CONFIG_FILENAMES,
        LoadedConfig,
        discover_config,
        load_config,
        parse_config,
    )
    from smelt.config.models import SmeltConfig


def __getattr__(name: str) -> Any:
    if name == "SmeltConfig":
        return getattr(import_module("smelt.config.models"), name)
    if name in __all__:
        return getattr(import_module("smelt.config.loader"), name)
    raise AttributeError(name)


__all__ = [
    "CONFIG_FILENAME",
    "CONFIG_FILENAMES",
    "ConfigError",
    "ConfigIssue",
    "LoadedConfig",
    "SmeltConfig",
    "discover_config",
    "load_config",
    "parse_config",
]
