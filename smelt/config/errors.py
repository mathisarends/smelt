from __future__ import annotations

import difflib
import os.path
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from smelt import __version__

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


def _upgrade_guide_url(version: str, *, checkout: bool) -> str:
    """Use the release tag for packaged releases and main for development builds."""
    ref = (
        f"v{version}"
        if not checkout
        and version != "0.0.0"
        and re.fullmatch(r"\d+\.\d+\.\d+", version)
        else "main"
    )
    return f"https://github.com/mathisarends/smelt/blob/{ref}/docs/upgrading.md"


UPGRADE_GUIDE_URL = _upgrade_guide_url(
    __version__, checkout=(Path(__file__).resolve().parents[2] / ".git").exists()
)


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    path: str
    message: str
    source: str | None = None
    semantic: bool = False

    def __str__(self) -> str:
        detail = f"{self.path}: {self.message}" if self.path else self.message
        return f"{self.source}: {detail}" if self.source else detail


class ConfigError(Exception):
    def __init__(
        self, issues: Sequence[ConfigIssue], source: str | None = None
    ) -> None:
        self.issues = tuple(issues)
        self.source = source
        header = (
            f"invalid configuration in {source}" if source else "invalid configuration"
        )
        lines = [header]
        guide = False
        for issue in self.issues:
            message = issue.message.removesuffix(
                f" (see {UPGRADE_GUIDE_URL})"
            ).removesuffix(f"; see {UPGRADE_GUIDE_URL}")
            guide |= message != issue.message
            visible = replace(
                issue,
                message=message,
                source=None if issue.source == source else issue.source,
            )
            lines.append(f"  {visible}")
        if guide:
            lines.append(f"  see {UPGRADE_GUIDE_URL}")
        super().__init__("\n".join(lines))

    def relative_to(self, root: Path) -> ConfigError:
        """Show declaring files relative to the project, once when all issues agree."""
        issues = [
            replace(issue, source=_relative_source(issue.source, root))
            for issue in self.issues
        ]
        source = _relative_source(self.source, root)
        files = {issue.source or source for issue in issues}
        if len(files) == 1:
            source = next(iter(files))
        if tuple(issues) == self.issues and source == self.source:
            return self
        return ConfigError(issues, source)


def format_loc(loc: Iterable[str | int]) -> str:
    """Render a location tuple as ``a.b[1].c``."""
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        elif parts:
            parts.append(f".{item}")
        else:
            parts.append(item)
    return "".join(parts)


def did_you_mean(value: str, candidates: Iterable[str]) -> str:
    options = sorted(candidates)
    matches = difflib.get_close_matches(value, options, n=1, cutoff=0.6)
    if not matches:
        matches = [option for option in options if option.startswith(value)][:1]
    return f' (did you mean "{matches[0]}"?)' if matches else ""


def _relative_source(source: str | None, root: Path) -> str | None:
    if source is None or not Path(source).is_absolute():
        return source
    try:
        relative_path = Path(os.path.relpath(source, root))
        return relative_path.as_posix()
    except ValueError:
        return source
